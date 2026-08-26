# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Schema versioning and *data* migrations.

Structural schema changes (new tables, new columns) are applied
automatically at every startup via ``Base.metadata.create_all`` +
additive ``ALTER TABLE`` in ``db/database.py`` — the app always runs
against the latest table shape.

*Data* migrations (backfilling derived data for existing rows — e.g.
parsing blocks out of raw bodies captured before the blocks table existed)
are NOT automatic: they can be slow and are only meaningful for rows that
still have their raw content. They are tracked here via the ``schema_meta``
table and applied explicitly with ``contextspy db-upgrade``.
"""
from __future__ import annotations

import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from contextspy.db.models import BlockRecord, Request, SchemaMeta, ToolStat

SCHEMA_VERSION = 3

_SCHEMA_VERSION_KEY = "schema_version"
_PENDING_KEY = "pending_data_migrations"


# ---------------------------------------------------------------------------
# schema_meta helpers
# ---------------------------------------------------------------------------

def get_meta(db: OrmSession, key: str, default: str | None = None) -> str | None:
    row = db.get(SchemaMeta, key)
    return row.value if row else default


def set_meta(db: OrmSession, key: str, value: str) -> None:
    row = db.get(SchemaMeta, key)
    if row:
        row.value = value
    else:
        db.add(SchemaMeta(key=key, value=value))
    db.flush()


def check_and_flag_pending_migrations(db: OrmSession) -> list[int]:
    """Ensure schema_meta reflects reality; return pending data-migration versions.

    - Empty DB (no requests yet): nothing to backfill, mark up to date.
    - Existing DB with no schema_meta row yet (upgrading from before this
      feature existed): flag every known data migration as pending.
    - Otherwise: return whatever is already recorded as pending.
    """
    stored_version = get_meta(db, _SCHEMA_VERSION_KEY)
    if stored_version is None:
        has_requests = db.execute(select(func.count()).select_from(Request)).scalar() or 0
        if has_requests == 0:
            set_meta(db, _SCHEMA_VERSION_KEY, str(SCHEMA_VERSION))
            set_meta(db, _PENDING_KEY, "[]")
            return []
        pending = sorted(_DATA_MIGRATIONS.keys())
        set_meta(db, _SCHEMA_VERSION_KEY, "1")
        set_meta(db, _PENDING_KEY, json.dumps(pending))
        return pending

    pending = set(json.loads(get_meta(db, _PENDING_KEY, "[]") or "[]"))
    try:
        current = int(stored_version)
    except ValueError:
        current = 1
    pending.update(version for version in _DATA_MIGRATIONS if version > current)
    ordered = sorted(pending)
    set_meta(db, _PENDING_KEY, json.dumps(ordered))
    return ordered


def apply_data_migrations(db: OrmSession) -> list[int]:
    """Run all pending data migrations in order. Returns the versions applied."""
    pending = json.loads(get_meta(db, _PENDING_KEY, "[]") or "[]")
    applied: list[int] = []
    for version in sorted(pending):
        fn = _DATA_MIGRATIONS.get(version)
        if fn is not None:
            fn(db)
            applied.append(version)
    set_meta(db, _SCHEMA_VERSION_KEY, str(SCHEMA_VERSION))
    set_meta(db, _PENDING_KEY, "[]")
    return applied


# ---------------------------------------------------------------------------
# v2: blocks table + session_seq backfill
# ---------------------------------------------------------------------------

def _backfill_session_seq(db: OrmSession) -> None:
    session_ids = db.execute(
        select(Request.session_id)
        .where(Request.session_id.isnot(None), Request.session_seq.is_(None))
        .distinct()
    ).scalars().all()
    for sid in session_ids:
        reqs = db.execute(
            select(Request).where(Request.session_id == sid).order_by(Request.timestamp.asc())
        ).scalars().all()
        for i, r in enumerate(reqs, start=1):
            if r.session_seq is None:
                r.session_seq = i
    db.flush()


def _backfill_blocks_from_raw_bodies(db: OrmSession) -> None:
    # Imported lazily to avoid a hard import-time dependency from db/ on analysis/.
    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.blocks import AnalyzedRequest
    from contextspy.analysis.classifier import classify, per_tool_tokens
    from contextspy.db.crud import insert_blocks, upsert_tool_stats

    already_done = set(db.execute(select(BlockRecord.request_id).distinct()).scalars().all())
    rows = db.execute(select(Request).where(Request.raw_request_body.isnot(None))).scalars().all()

    for row in rows:
        if row.id in already_done:
            continue
        adapter = get_adapter(row.endpoint)
        if adapter is None:
            continue
        try:
            req_body = json.loads(row.raw_request_body)
        except (json.JSONDecodeError, TypeError):
            continue
        try:
            resp_body = json.loads(row.raw_response_body) if row.raw_response_body else {}
        except json.JSONDecodeError:
            resp_body = {}

        input_blocks, tool_call_map = adapter.parse_request(req_body)
        output_blocks, usage = adapter.parse_response(resp_body)
        analyzed = AnalyzedRequest(
            model=req_body.get("model"),
            input_blocks=input_blocks,
            output_blocks=output_blocks,
            usage=usage,
            tool_call_map=tool_call_map,
        )
        breakdown = classify(analyzed)
        for field, value in breakdown.to_db_fields().items():
            setattr(row, field, value)

        insert_blocks(db, row.id, input_blocks + output_blocks)

        tool_rows = per_tool_tokens(analyzed)
        if tool_rows:
            existing = db.execute(
                select(func.count()).select_from(ToolStat).where(ToolStat.request_id == row.id)
            ).scalar()
            if not existing:
                upsert_tool_stats(db, row.id, tool_rows)

    db.flush()


def _migrate_to_v2(db: OrmSession) -> None:
    _backfill_session_seq(db)
    _backfill_blocks_from_raw_bodies(db)


# ---------------------------------------------------------------------------
# v3: logical requests, response lineage, and effective context snapshots
# ---------------------------------------------------------------------------

def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _backfill_response_complete(db: OrmSession) -> None:
    rows = db.execute(select(Request).where(Request.response_complete == 0)).scalars().all()
    for row in rows:
        usage_extra = _json_object(row.usage_extra)
        if usage_extra.get("ws_incomplete"):
            continue
        if row.capture_error:
            continue
        if (
            row.transport != "websocket"
            and row.status_code is not None
            and 200 <= row.status_code < 400
        ):
            row.response_complete = 1
            continue
        response = _json_object(row.raw_response_body)
        if not response:
            continue
        status = response.get("status")
        if status in ("failed", "incomplete", "cancelled"):
            continue
        # Legacy buffered responses and reconstructed terminal responses both
        # have a response object but predate the completion column.
        row.response_complete = 1
    db.flush()


def _backfill_logical_requests(db: OrmSession) -> None:
    import uuid

    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.blocks import AnalyzedRequest, Usage
    from contextspy.analysis.classifier import classify
    from contextspy.analysis.context_reconstruction import reconstruct_context
    from contextspy.analysis.conversation import (
        ContextMutation,
        InvocationIdentity,
        get_conversation_adapter,
    )
    from contextspy.db import crud

    rows = db.execute(select(Request).order_by(Request.timestamp.asc())).scalars().all()
    touched_groups: set[str] = set()
    for row in rows:
        if row.logical_request_id:
            touched_groups.add(row.logical_request_id)
            continue
        request_body = _json_object(row.raw_request_body)
        response_body = _json_object(row.raw_response_body)
        conversation_adapter = (
            get_conversation_adapter(
                provider=row.provider,
                endpoint=row.endpoint,
                transport=row.transport,
                request_body=request_body,
            ) if request_body else None
        )
        identity = (
            conversation_adapter.identify(
                provider=row.provider,
                agent=row.agent or "unknown",
                request_body=request_body,
                response_body=response_body,
            ) if conversation_adapter else InvocationIdentity(
                agent_id=row.agent, confidence="singleton",
            )
        )
        mutation = (
            conversation_adapter.context_mutation(
                request_body=request_body, identity=identity,
            ) if conversation_adapter else ContextMutation()
        )
        predecessor = None
        if identity.previous_provider_request_id:
            predecessor = crud.get_request_by_provider_id(
                db, row.provider, identity.previous_provider_request_id,
            )

        logical_key = (
            conversation_adapter.logical_request_key(
                provider=row.provider, identity=identity,
            ) if conversation_adapter else None
        )
        scoped_logical_key = (
            json.dumps([logical_key.serialize(), row.session_id], separators=(",", ":"))
            if logical_key else None
        )
        logical = (
            crud.get_logical_request_by_key(db, scoped_logical_key)
            if scoped_logical_key else None
        )
        if (
            logical is None and logical_key is None and predecessor
            and predecessor.session_id == row.session_id
            and predecessor.logical_request_id
        ):
            logical = crud.get_logical_request(db, predecessor.logical_request_id)
        if logical is None:
            logical = crud.create_logical_request(db, {
                "id": str(uuid.uuid4()),
                "session_id": row.session_id,
                "grouping_key": (
                    scoped_logical_key if scoped_logical_key
                    else f"legacy-singleton:{row.id}"
                ),
                "provider": row.provider,
                "agent": identity.agent_id or row.agent,
                "model": row.model,
                "endpoint": row.endpoint,
                "transport": row.transport,
                "provider_conversation_id": identity.provider_conversation_id,
                "logical_turn_id": identity.logical_turn_id,
                "started_at": row.timestamp,
                "updated_at": row.timestamp,
                "state": "complete" if row.response_complete else "incomplete",
                "grouping_confidence": (
                    identity.confidence if logical_key else "singleton"
                ),
                "grouping_metadata": (
                    json.dumps(identity.metadata) if identity.metadata else None
                ),
            })
        if (
            identity.parent_turn_id
            and identity.provider_conversation_id
            and logical.parent_logical_request_id is None
        ):
            parent = crud.get_logical_request_by_turn(
                db,
                provider=row.provider,
                conversation_id=identity.provider_conversation_id,
                turn_id=identity.parent_turn_id,
                session_id=row.session_id,
            )
            if parent is not None and parent.id != logical.id:
                logical.parent_logical_request_id = parent.id

        row.logical_request_id = logical.id
        row.provider_request_id = identity.provider_request_id
        row.previous_provider_request_id = identity.previous_provider_request_id
        row.provider_conversation_id = identity.provider_conversation_id
        row.logical_turn_id = identity.logical_turn_id
        row.invocation_seq = crud.next_invocation_seq(db, logical.id)
        row.identity_metadata = json.dumps({
            "adapter": conversation_adapter.adapter_id if conversation_adapter else None,
            **identity.metadata,
        })
        row.observed_input_tokens = row.tokens_total_input
        db.flush()

        adapter = get_adapter(row.endpoint)
        if adapter is not None and request_body:
            try:
                input_blocks, tool_map = adapter.parse_request(request_body)
                output_blocks, usage = (
                    adapter.parse_response(response_body)
                    if response_body else ([], Usage())
                )
                analyzed = AnalyzedRequest(
                    model=row.model,
                    input_blocks=input_blocks,
                    output_blocks=output_blocks,
                    usage=usage,
                    tool_call_map=tool_map,
                )
                classify(analyzed)
                reconstruct_context(
                    db,
                    request=row,
                    analyzed=analyzed,
                    identity=identity,
                    mutation=mutation,
                    predecessor=predecessor,
                )
            except Exception:
                row.context_reconstruction_status = "migration_failed"
        touched_groups.add(logical.id)

    for logical_id in touched_groups:
        crud.refresh_logical_request(db, logical_id)
    db.flush()


def _migrate_to_v3(db: OrmSession) -> None:
    _backfill_response_complete(db)
    _backfill_logical_requests(db)


_DATA_MIGRATIONS: dict[int, Callable[[OrmSession], None]] = {
    2: _migrate_to_v2,
    3: _migrate_to_v3,
}
