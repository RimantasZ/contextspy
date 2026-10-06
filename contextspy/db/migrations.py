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
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session as OrmSession

from contextspy.db.models import BlockRecord, Request, SchemaMeta, Session, ToolStat

SCHEMA_VERSION = 10

logger = logging.getLogger(__name__)

_SCHEMA_VERSION_KEY = "schema_version"
_PENDING_KEY = "pending_data_migrations"


# ---------------------------------------------------------------------------
# schema_meta helpers
# ---------------------------------------------------------------------------

def _pending_versions(
    version_from: int, recorded_pending: list[int] | None = None
) -> list[int]:
    """Return recorded and newly-required migrations in version order."""
    pending = set(recorded_pending or [])
    pending.update(version for version in _DATA_MIGRATIONS if version > version_from)
    return sorted(pending)


def inspect_migration_state(db_path: Path) -> tuple[int, list[int]]:
    """Read the current version and pending migrations without changing the DB.

    Legacy databases with requests but no ``schema_meta`` value are version 1.
    A missing or unused database has no data to migrate and is treated as current.
    The read-only connection is important: ``db-upgrade`` uses this result to make
    a byte-for-byte backup before ``init_db`` can apply structural changes.
    """
    db_path = Path(db_path)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return SCHEMA_VERSION, []

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        stored_version: str | None = None
        recorded_pending: list[int] = []
        if "schema_meta" in tables:
            values = dict(
                conn.execute(
                    "SELECT key, value FROM schema_meta WHERE key IN (?, ?)",
                    (_SCHEMA_VERSION_KEY, _PENDING_KEY),
                )
            )
            stored_version = values.get(_SCHEMA_VERSION_KEY)
            recorded_pending = json.loads(values.get(_PENDING_KEY, "[]") or "[]")

        if stored_version is None:
            has_requests = (
                "requests" in tables
                and (conn.execute("SELECT COUNT(*) FROM requests").fetchone() or (0,))[0] > 0
            )
            if not has_requests:
                return SCHEMA_VERSION, []
            version_from = 1
        else:
            version_from = int(stored_version)

    return version_from, _pending_versions(version_from, recorded_pending)


def create_migration_backup(
    db_path: Path,
    version_from: int,
    version_to: int,
    *,
    timestamp: datetime | None = None,
) -> Path:
    """Create a consistent SQLite snapshot, including committed WAL pages."""
    from contextspy.db.backups import create_backup

    return create_backup(
        db_path, version_from, purpose="migration",
        target_version=version_to, timestamp=timestamp,
    )


def list_migration_backups(db_path: Path) -> list[Path]:
    """Return versioned migration backups belonging to ``db_path``."""
    from contextspy.db.backups import list_backups

    db_path = Path(db_path)
    escaped_stem = re.escape(db_path.stem)
    migration = re.compile(
        rf"{escaped_stem}_backup_v\d+_to_v\d+_"
        r"\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?\.back"
    )
    legacy = re.compile(rf"{escaped_stem}_\d+_\d+_\d{{8}}T\d{{12}}Z\.back")
    return [
        path for path in list_backups(db_path)
        if migration.fullmatch(path.name) or legacy.fullmatch(path.name)
    ]


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
    - Otherwise: merge recorded pending migrations with every known migration
      newer than the stored version.
    """
    stored_version = get_meta(db, _SCHEMA_VERSION_KEY)
    if stored_version is None:
        has_requests = db.execute(select(func.count()).select_from(Request)).scalar() or 0
        if has_requests == 0:
            set_meta(db, _SCHEMA_VERSION_KEY, str(SCHEMA_VERSION))
            set_meta(db, _PENDING_KEY, "[]")
            return []
        pending = _pending_versions(1)
        set_meta(db, _SCHEMA_VERSION_KEY, "1")
        set_meta(db, _PENDING_KEY, json.dumps(pending))
        return pending

    version_from = int(stored_version)
    recorded_pending = json.loads(get_meta(db, _PENDING_KEY, "[]") or "[]")
    pending = _pending_versions(version_from, recorded_pending)
    if pending != recorded_pending:
        set_meta(db, _PENDING_KEY, json.dumps(pending))
    return pending


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
# v3: retain canonical provider JSON independently from transport evidence
# ---------------------------------------------------------------------------

def _is_json_object(text_value: str | None) -> bool:
    if not text_value:
        return False
    try:
        return isinstance(json.loads(text_value), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def _backfill_canonical_bodies(db: OrmSession) -> None:
    rows = db.execute(select(Request)).scalars().all()
    for row in rows:
        if row.canonical_request_body is None and _is_json_object(row.raw_request_body):
            row.canonical_request_body = row.raw_request_body
        if row.canonical_response_body is None and _is_json_object(row.raw_response_body):
            row.canonical_response_body = row.raw_response_body

        if row.invocation_outcome == "unknown":
            if row.status_code is not None and row.status_code >= 400:
                row.invocation_outcome = "failed"
            elif row.status_code is not None and 200 <= row.status_code < 300:
                row.invocation_outcome = "completed"
            elif row.transport == "websocket" and not row.response_complete:
                row.invocation_outcome = "incomplete"

        # Old stateful rows contain only the sparse observed request. Retain it
        # for diagnostics but do not present it as a complete reconstructed
        # context until an exact-ID backfill has actually expanded the chain.
        if row.transport == "websocket" and row.canonical_request_body:
            try:
                request_value = json.loads(row.canonical_request_body)
            except (json.JSONDecodeError, TypeError):
                continue
            predecessor = request_value.get("previous_response_id")
            if isinstance(predecessor, str) and predecessor:
                row.predecessor_response_id = row.predecessor_response_id or predecessor
                row.context_fidelity = "partial"
                if row.context_notes is None:
                    row.context_notes = json.dumps([
                        "Existing capture has not been reconstructed from its predecessor"
                    ])
    db.flush()


def _decode_captured_events(value: str | None):
    from contextspy.analysis.capture import CapturedEvent

    if not value:
        return []
    try:
        raw_events = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw_events, list):
        return []
    fields = {
        "sequence", "direction", "kind", "payload", "text", "event",
        "event_id", "retry_ms", "comments", "done",
    }
    events = []
    for index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            continue
        data = {key: value for key, value in raw_event.items() if key in fields}
        data.setdefault("sequence", index)
        events.append(CapturedEvent(**data))
    return events


def _backfill_responses_websocket_rows(db: OrmSession) -> None:
    """Rebuild retained WS rows only through exact Responses provider IDs."""
    from dataclasses import replace

    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.classifier import classify, per_tool_tokens
    from contextspy.analysis.invocations import (
        CanonicalJsonDocument,
        analyze_invocation,
    )
    from contextspy.db.crud import insert_blocks, upsert_tool_stats
    from contextspy.normalization import (
        ObservedInvocation,
        PersistedCanonicalInvocation,
        normalize_invocation,
    )

    class MemoryLineage:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], PersistedCanonicalInvocation] = {}

        def get(self, provider: str, response_id: str):
            return self.values.get((provider, response_id))

        def put(self, provider: str, response_id: str, value) -> None:
            self.values[(provider, response_id)] = value

    candidates = []
    rows = db.execute(
        select(Request).where(Request.transport == "websocket")
    ).scalars().all()
    for row in rows:
        adapter = get_adapter(row.endpoint)
        if adapter is None or adapter.format_id != "openai_responses":
            continue
        request_text = row.raw_request_body or row.canonical_request_body
        if not request_text:
            continue
        try:
            request_payload = json.loads(request_text)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(request_payload, dict):
            continue

        events = _decode_captured_events(row.response_events)
        response_document = None
        if events:
            try:
                reconstructed = adapter.reconstruct_response(events, transport="websocket")
                response_document = CanonicalJsonDocument.from_value(reconstructed.payload)
            except Exception:
                response_document = None
        if response_document is None:
            response_text = row.canonical_response_body or row.raw_response_body
            if response_text:
                try:
                    response_document = CanonicalJsonDocument.from_text(response_text)
                except (ValueError, TypeError, json.JSONDecodeError):
                    response_document = None

        predecessor = request_payload.get("previous_response_id")
        predecessor = predecessor if isinstance(predecessor, str) and predecessor else None
        candidates.append((row, adapter, request_text, request_payload, events, response_document, predecessor))

    lineage = MemoryLineage()
    pending = list(candidates)

    def apply_candidate(candidate, *, allow_missing: bool) -> bool:
        row, adapter, request_text, request_payload, events, response_document, predecessor = candidate
        if predecessor and lineage.get(row.provider, predecessor) is None and not allow_missing:
            return False
        outcome = row.invocation_outcome
        if outcome == "unknown":
            if row.status_code is not None and row.status_code >= 400:
                outcome = "failed"
            elif not row.response_complete:
                outcome = "incomplete"
            else:
                response_status = response_document.value.get("status") if response_document else None
                outcome = (
                    "failed" if response_status == "failed"
                    else "incomplete" if response_status == "incomplete"
                    else "completed"
                )

        canonical = normalize_invocation(
            ObservedInvocation(
                provider=row.provider,
                provider_protocol=adapter.format_id,
                protocol_id="codex_responses",
                request_payload=request_payload,
                observed_request_text=request_text,
                response=response_document,
                events=tuple(events),
                outcome=outcome,
            ),
            lineage,
        )
        if (
            not events
            and canonical.response is not None
            and canonical.response.value.get("output") == []
            and canonical.context_fidelity == "complete"
        ):
            canonical = replace(
                canonical,
                context_fidelity="partial",
                context_notes=canonical.context_notes + (
                    "The retained response has no output-event evidence",
                ),
            )
        analysis = analyze_invocation(canonical, adapter)

        row.canonical_request_body = canonical.request.text
        row.canonical_response_body = canonical.response.text if canonical.response else None
        row.provider_response_id = canonical.provider_response_id
        row.predecessor_response_id = canonical.predecessor_response_id
        row.invocation_outcome = canonical.outcome
        row.context_fidelity = canonical.context_fidelity
        row.context_notes = json.dumps(canonical.context_notes) if canonical.context_notes else None

        # A provider-schema parser failure should not destroy previously useful
        # derived data. Canonical bodies remain retained for a later retry.
        if not analysis.issues:
            analyzed = analysis.analyzed
            breakdown = classify(analyzed)
            for field, value in breakdown.to_db_fields().items():
                setattr(row, field, value)
            row.model = analyzed.model
            row.provider_input_tokens = analyzed.usage.input_tokens
            row.provider_output_tokens = analyzed.usage.output_tokens
            row.provider_reasoning_tokens = analyzed.usage.reasoning_tokens
            row.cache_read_tokens = analyzed.usage.cache_read_tokens
            row.cache_creation_tokens = analyzed.usage.cache_creation_tokens
            row.usage_extra = (
                json.dumps(analyzed.usage.extra) if analyzed.usage.extra else None
            )
            db.execute(delete(BlockRecord).where(BlockRecord.request_id == row.id))
            db.execute(delete(ToolStat).where(ToolStat.request_id == row.id))
            all_blocks = analyzed.input_blocks + analyzed.output_blocks
            if all_blocks:
                insert_blocks(db, row.id, all_blocks)
            tool_rows = per_tool_tokens(analyzed)
            if tool_rows:
                upsert_tool_stats(db, row.id, tool_rows)

        if canonical.provider_response_id:
            lineage.put(
                row.provider,
                canonical.provider_response_id,
                PersistedCanonicalInvocation(
                    request=canonical.request,
                    response=canonical.response,
                    context_fidelity=canonical.context_fidelity,
                ),
            )
        return True

    # Roots and resolved descendants first. Remaining nodes are normalized as
    # explicitly partial; no adjacency or timestamp inference is permitted.
    while pending:
        next_pending = []
        progressed = False
        for candidate in pending:
            if apply_candidate(candidate, allow_missing=False):
                progressed = True
            else:
                next_pending.append(candidate)
        pending = next_pending
        if not progressed:
            break
    for candidate in pending:
        apply_candidate(candidate, allow_missing=True)
    db.flush()


def _migrate_to_v3(db: OrmSession) -> None:
    _backfill_canonical_bodies(db)
    _backfill_responses_websocket_rows(db)


# ---------------------------------------------------------------------------
# v4: exclude inline media transport encodings from text-token estimates
# ---------------------------------------------------------------------------

def _reanalyze_inline_media_requests(db: OrmSession) -> None:
    """Rebuild input analysis for retained requests containing inline media."""
    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.adapters.base import contains_media_content
    from contextspy.analysis.blocks import AnalyzedRequest, Usage
    from contextspy.analysis.classifier import classify, per_tool_tokens
    from contextspy.analysis.tokenizer import TOKENIZER_ID
    from contextspy.db.crud import insert_blocks, upsert_tool_stats

    rows = db.execute(
        select(Request).where(
            Request.canonical_request_body.isnot(None),
            Request.canonical_request_body.contains(";base64,"),
        )
    ).scalars().all()

    input_breakdown_fields = (
        "tokens_system_prompt",
        "tokens_tool_definitions",
        "tokens_tool_results",
        "tokens_file_contents",
        "tokens_conversation_history",
        "tokens_current_user_message",
        "tokens_assistant_prefill",
        "tokens_uncategorized",
        "tokens_total_input",
    )

    for row in rows:
        adapter = get_adapter(row.endpoint)
        if adapter is None:
            continue
        try:
            request_value = json.loads(row.canonical_request_body)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(request_value, dict) or not contains_media_content(request_value):
            continue

        try:
            input_blocks, tool_call_map = adapter.parse_request(request_value)
        except Exception:
            continue
        analyzed = AnalyzedRequest(
            model=request_value.get("model"),
            input_blocks=input_blocks,
            output_blocks=[],
            usage=Usage(),
            tool_call_map=tool_call_map,
        )
        breakdown = classify(analyzed)
        breakdown_fields = breakdown.to_db_fields()
        for field in input_breakdown_fields:
            setattr(row, field, breakdown_fields[field])
        row.tokenizer = TOKENIZER_ID

        db.execute(delete(BlockRecord).where(
            BlockRecord.request_id == row.id,
            BlockRecord.direction == "input",
        ))
        db.execute(delete(ToolStat).where(ToolStat.request_id == row.id))
        if input_blocks:
            insert_blocks(db, row.id, input_blocks)
        tool_rows = per_tool_tokens(analyzed)
        if tool_rows:
            upsert_tool_stats(db, row.id, tool_rows)

    db.flush()


def _migrate_to_v4(db: OrmSession) -> None:
    _reanalyze_inline_media_requests(db)


# ---------------------------------------------------------------------------
# v5: concurrency-safe capture-local request numbering
# ---------------------------------------------------------------------------

def _migrate_to_v5(db: OrmSession) -> None:
    sessions = db.execute(select(Session)).scalars().all()
    for session in sessions:
        requests = db.execute(
            select(Request)
            .where(Request.session_id == session.id)
            .order_by(Request.session_seq.asc().nullslast(), Request.timestamp.asc(), Request.id.asc())
        ).scalars().all()
        sequences = [request.session_seq for request in requests]
        needs_repair = any(value is None for value in sequences) or len(set(sequences)) != len(sequences)
        if needs_repair:
            # Only damaged captures are renumbered. Valid historical labels,
            # including gaps, are immutable.
            for temporary, request in enumerate(requests, start=1):
                request.session_seq = -temporary
            db.flush()
            for sequence, request in enumerate(requests, start=1):
                request.session_seq = sequence
            db.flush()
            sequences = list(range(1, len(requests) + 1))
        session.next_request_seq = max((value for value in sequences if value is not None), default=0) + 1
    db.flush()
    db.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_session_seq_unique "
        "ON requests (session_id, session_seq)"
    ))


# ---------------------------------------------------------------------------
# v6: durable, source-labelled conversation stream hints
# ---------------------------------------------------------------------------

def _migrate_to_v6(db: OrmSession) -> None:
    """Backfill only the compact hint; never retain request bodies here.

    Keyset batches bound Python memory even when a retained canonical request
    is large. The migration runner owns the transaction and backup; on an
    interrupted run it rolls back and this idempotent function can be retried.
    """
    from contextspy.analysis.stream_hint import extract_stream_hint

    last_id = ""
    while True:
        rows = db.execute(
            select(
                Request.id, Request.agent, Request.endpoint,
                Request.canonical_request_body, Request.raw_request_body,
            )
            .where(
                Request.id > last_id,
                Request.stream_hint_digest.is_(None),
                (Request.canonical_request_body.isnot(None)
                 | Request.raw_request_body.isnot(None)),
            )
            .order_by(Request.id)
            .limit(10)
        ).all()
        if not rows:
            break
        for row in rows:
            last_id = row.id
            source = digest = None
            for body in (row.canonical_request_body, row.raw_request_body):
                if not body:
                    continue
                try:
                    value = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(value, dict):
                    source, digest = extract_stream_hint(
                        agent=row.agent, endpoint=row.endpoint, request=value,
                    )
                    if digest:
                        break
            if digest:
                db.execute(
                    update(Request).where(Request.id == row.id).values(
                        stream_hint_source=source, stream_hint_digest=digest,
                    )
                )
    db.flush()


def _migrate_to_v7(db: OrmSession) -> None:
    """Re-analyze retained Anthropic thread requests through exact ID lineage."""
    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.classifier import classify, per_tool_tokens
    from contextspy.analysis.invocations import CanonicalJsonDocument, analyze_invocation
    from contextspy.db.crud import (
        get_unique_request_by_provider_response_id,
        insert_blocks,
        upsert_tool_stats,
    )
    from contextspy.normalization import (
        ObservedInvocation,
        PersistedCanonicalInvocation,
        normalize_invocation,
    )

    class DatabaseLineage:
        def get(self, provider: str, response_id: str):
            parent = get_unique_request_by_provider_response_id(db, provider, response_id)
            if parent is None:
                return None
            request_text = parent.canonical_request_body or parent.raw_request_body
            response_text = parent.canonical_response_body or parent.raw_response_body
            if not request_text:
                return None
            try:
                request = CanonicalJsonDocument.from_text(request_text)
                response = (
                    CanonicalJsonDocument.from_text(response_text)
                    if response_text else None
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                return None
            adapter = get_adapter(parent.endpoint)
            return PersistedCanonicalInvocation(
                request=request,
                response=response,
                context_fidelity=parent.context_fidelity,
                outcome=parent.invocation_outcome,
                provider_protocol=adapter.format_id if adapter else None,
            )

    class MissingLineage:
        def get(self, provider: str, response_id: str):
            return None

    candidates: dict[str, tuple[str, str | None]] = {}
    response_to_candidate: dict[tuple[str, str], str] = {}
    rows = db.execute(
        select(Request.id, Request.provider, Request.provider_response_id,
               Request.endpoint, Request.raw_request_body)
        .where(Request.raw_request_body.contains('"thread"'))
    )
    for request_id, provider, response_id, endpoint, raw_text in rows:
        adapter = get_adapter(endpoint)
        if adapter is None or adapter.format_id != "anthropic":
            continue
        try:
            payload = json.loads(raw_text)
        except (ValueError, TypeError):
            continue
        thread = payload.get("thread") if isinstance(payload, dict) else None
        if not isinstance(thread, dict):
            continue
        predecessor = thread.get("previous_message_id")
        predecessor = predecessor if isinstance(predecessor, str) and predecessor else None
        candidates[request_id] = (provider, predecessor)
        if response_id:
            response_to_candidate[(provider, response_id)] = request_id

    remaining = dict(candidates)
    counts = {
        "retained": len(candidates), "reanalyzed": 0, "already_full": 0,
        "missing_ancestor": 0, "partial": 0, "opaque": 0, "failed": 0,
        "canonical_growth_bytes": 0,
    }
    lineage = DatabaseLineage()
    missing_lineage = MissingLineage()

    def apply_candidate(request_id: str, *, force_missing: bool = False) -> None:
        row = db.get(Request, request_id)
        if row is None or not row.raw_request_body:
            counts["failed"] += 1
            return
        adapter = get_adapter(row.endpoint)
        try:
            payload = json.loads(row.raw_request_body)
            response_text = row.canonical_response_body or row.raw_response_body
            response = (
                CanonicalJsonDocument.from_text(response_text)
                if response_text else None
            )
            outcome = row.invocation_outcome
            if outcome == "unknown":
                outcome = (
                    "failed" if row.status_code is not None and row.status_code >= 400
                    else "incomplete" if not row.response_complete
                    else "completed"
                )
            canonical = normalize_invocation(
                ObservedInvocation(
                    provider=row.provider,
                    provider_protocol="anthropic",
                    protocol_id="anthropic_messages",
                    request_payload=payload,
                    observed_request_text=row.raw_request_body,
                    response=response,
                    outcome=outcome,
                ),
                missing_lineage if force_missing else lineage,
            )
            analysis = analyze_invocation(canonical, adapter)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("Skipping Anthropic thread backfill for %s: %s", request_id, exc)
            counts["failed"] += 1
            return

        old_canonical_bytes = len((row.canonical_request_body or "").encode("utf-8"))
        row.canonical_request_body = canonical.request.text
        counts["canonical_growth_bytes"] += (
            len(canonical.request.text.encode("utf-8")) - old_canonical_bytes
        )
        row.canonical_response_body = (
            canonical.response.text if canonical.response else None
        )
        row.provider_response_id = canonical.provider_response_id
        row.predecessor_response_id = canonical.predecessor_response_id
        row.invocation_outcome = canonical.outcome
        row.context_fidelity = canonical.context_fidelity
        row.context_notes = (
            json.dumps(canonical.context_notes) if canonical.context_notes else None
        )
        if analysis.issues:
            counts["failed"] += 1
        else:
            analyzed = analysis.analyzed
            breakdown = classify(analyzed)
            for field, value in breakdown.to_db_fields().items():
                setattr(row, field, value)
            row.model = analyzed.model
            row.provider_input_tokens = analyzed.usage.input_tokens
            row.provider_output_tokens = analyzed.usage.output_tokens
            row.provider_reasoning_tokens = analyzed.usage.reasoning_tokens
            row.cache_read_tokens = analyzed.usage.cache_read_tokens
            row.cache_creation_tokens = analyzed.usage.cache_creation_tokens
            row.usage_extra = (
                json.dumps(analyzed.usage.extra) if analyzed.usage.extra else None
            )
            db.execute(delete(BlockRecord).where(BlockRecord.request_id == row.id))
            db.execute(delete(ToolStat).where(ToolStat.request_id == row.id))
            blocks = analyzed.input_blocks + analyzed.output_blocks
            if blocks:
                insert_blocks(db, row.id, blocks)
            tool_rows = per_tool_tokens(analyzed)
            if tool_rows:
                upsert_tool_stats(db, row.id, tool_rows)
            counts["reanalyzed"] += 1
        if canonical.context_fidelity in {"partial", "opaque"}:
            counts[canonical.context_fidelity] += 1
        if payload.get("thread", {}).get("type") == "create":
            counts["already_full"] += 1
        if any("referenced earlier thread response" in note for note in canonical.context_notes):
            counts["missing_ancestor"] += 1
        db.flush()
        db.expunge(row)

    # Roots before descendants.  A missing/ambiguous ancestor is eventually
    # processed as partial; no timestamp or session order substitutes for it.
    while remaining:
        progressed = False
        for request_id, (provider, predecessor) in list(remaining.items()):
            parent_id = response_to_candidate.get((provider, predecessor)) if predecessor else None
            if parent_id in remaining and parent_id != request_id:
                continue
            apply_candidate(request_id)
            del remaining[request_id]
            progressed = True
        if not progressed:
            # Break one cyclic dependency as explicitly missing, then let
            # later descendants inherit only the retained partial tail.
            request_id = next(iter(remaining))
            apply_candidate(request_id, force_missing=True)
            del remaining[request_id]
    db.info["anthropic_thread_backfill"] = counts
    logger.info("Anthropic thread backfill: %s", counts)


# ---------------------------------------------------------------------------
# v8: per-request system-prompt headers are not part of block identity
# ---------------------------------------------------------------------------

def _migrate_to_v8(db: OrmSession) -> None:
    """Re-key retained system prompts that start with a per-request header.

    Claude Code's ``x-anthropic-billing-header`` line changes on every request, so the
    same system prompt used to get a new content hash each time. For each retained
    system-prompt block whose stored content starts with such a header, store the stable
    text under its own hash, point the block at it and keep the header in
    ``attrs["volatile_header"]``. ``token_count`` is left alone (it was counted on the
    full text). Blocks whose content was already purged cannot be recovered. Idempotent:
    a rewritten block no longer starts with the header. Keyset batches bound memory.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from contextspy.analysis.blocks import content_hash, split_volatile_header
    from contextspy.db.models import BlockContent

    replaced_hashes: set[str] = set()
    last_id = 0
    while True:
        rows = db.execute(
            select(BlockRecord.id, BlockRecord.content_hash, BlockRecord.attrs, BlockContent.content)
            .join(BlockContent, BlockRecord.content_hash == BlockContent.hash)
            .where(
                BlockRecord.id > last_id,
                BlockRecord.block_type == "system_prompt",
                BlockRecord.direction == "input",
                BlockContent.content.like("x-anthropic-billing-header:%"),
            )
            .order_by(BlockRecord.id)
            .limit(200)
        ).all()
        if not rows:
            break
        for row in rows:
            last_id = row.id
            stable, header = split_volatile_header(row.content)
            if header is None:
                continue
            new_hash = content_hash(stable)
            if new_hash is not None:
                db.execute(sqlite_insert(BlockContent).values(
                    hash=new_hash, content=stable, created_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing(index_elements=["hash"]))
            try:
                attrs = json.loads(row.attrs) if row.attrs else {}
            except json.JSONDecodeError:
                attrs = {}
            attrs["volatile_header"] = header
            db.execute(
                update(BlockRecord).where(BlockRecord.id == row.id)
                .values(content_hash=new_hash, attrs=json.dumps(attrs))
            )
            replaced_hashes.add(row.content_hash)
        db.flush()

    # The per-request copies are now unreferenced; drop them instead of waiting for retention
    # cleanup, which does not run when block-content retention is disabled.
    for old_hash in replaced_hashes:
        still_used = db.execute(
            select(BlockRecord.id).where(BlockRecord.content_hash == old_hash).limit(1)
        ).first()
        if still_used is None:
            db.execute(delete(BlockContent).where(BlockContent.hash == old_hash))
    db.flush()
    logger.info("v8: re-keyed system prompts with a volatile header (%d content copies)", len(replaced_hashes))


# ---------------------------------------------------------------------------
# v9: request purpose, block source keys and JSON locations
# ---------------------------------------------------------------------------

_V9_BATCH_REQUESTS = 100
_V9_PROGRESS_EVERY = 1000

# ``contextspy db-upgrade`` sets this to print progress; the log always receives it.
progress_reporter: Callable[[str], None] | None = None


def _report(message: str) -> None:
    logger.info(message)
    if progress_reporter is not None:
        progress_reporter(message)


def _v9_match_json_paths(stored: list, parsed: list) -> dict[int, str]:
    """Map block-row id -> json_path JSON for one direction, or {} when matching is ambiguous.

    Unambiguous means the same number of blocks and, position by position, the same block type
    and content hash. Anything else (an adapter that changed since capture, a purged document that
    no longer matches) leaves every block of the direction without a path. Never matches by content.
    """
    if len(stored) != len(parsed):
        return {}
    for row, block in zip(stored, parsed):
        if str(row.block_type) != str(block.block_type) or row.content_hash != block.content_hash:
            return {}
    return {
        row.id: json.dumps(list(block.json_path), separators=(",", ":"))
        for row, block in zip(stored, parsed)
        if row.json_path is None and block.json_path is not None
    }


def _migrate_to_v9(db: OrmSession) -> None:
    """Backfill request purpose, block ``source_key`` and block ``json_path`` (plans/wi0-data-foundation.md section 8)."""
    _backfill_classification(db, "v9", json_paths=True)


def _migrate_to_v10(db: OrmSession) -> None:
    """Re-derive source keys and ``blocks.file_path`` for requests classified before ``CLASSIFIER_VERSION`` 2.

    Same phase A as v9 (a database upgraded straight from before v9 has already done it, so this finds
    nothing to do). File paths come from retained tool-call content only; purged tool calls keep
    ``file_path`` NULL, so historical coverage is partial by nature (plans/file-paths.md).
    """
    _backfill_classification(db, "v10", json_paths=False)


def _backfill_classification(db: OrmSession, label: str, *, json_paths: bool) -> None:
    """Shared batched backfill behind v9 and v10 (``label`` prefixes progress lines and ``db.info`` key).

    A. Requests with ``classifier_version`` below the current one get ``purpose`` /
       ``purpose_detail`` and every block a ``source_key``, computed from the stored block rows by the
       same functions capture uses. Tool-call arguments are only available where block content has not
       been purged; elsewhere the generic ``tool:<name>`` key is stored. Requests without block rows
       are left untouched.
       Tool calls and their results also get ``file_path`` (analysis/paths.py).
    B. (``json_paths`` only) Where a canonical request/response document is still retained, the adapter
       re-parses it and ``json_path`` is copied onto the stored blocks if (and only if) the parse matches
       them exactly.

    Keyset batches keep memory bounded; re-running is cheap and writes nothing new. Progress goes to
    the log and to ``progress_reporter``; a summary lands in ``db.info["<label>_backfill"]``.
    """
    from sqlalchemy import and_

    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.blocks import BlockSnapshot
    from contextspy.analysis.purpose import CLASSIFIER_VERSION, PurposeInputs, derive_purpose
    from contextspy.analysis.sources import resolve_sources
    from contextspy.db.models import BlockContent

    total = db.execute(select(func.count()).select_from(Request)).scalar() or 0
    stats = {
        "requests": total, "classified": 0, "skipped_no_blocks": 0, "source_keys": 0,
        "paths_set": 0, "path_mismatch": 0, "path_failed": 0,
    }
    _report(
        f"{label}: backfilling {total:,} requests "
        + ("(purpose, source keys, file paths, JSON paths)" if json_paths else "(source keys, file paths)")
    )
    last_id = ""
    done = 0
    while True:
        requests = db.execute(
            select(
                Request.id, Request.agent, Request.endpoint, Request.classifier_version,
                Request.canonical_request_body.isnot(None).label("has_request_doc"),
                Request.canonical_response_body.isnot(None).label("has_response_doc"),
            ).where(Request.id > last_id).order_by(Request.id).limit(_V9_BATCH_REQUESTS)
        ).all()
        if not requests:
            break
        last_id = requests[-1].id

        rows_by_request: dict[str, list] = {}
        block_rows = db.execute(
            select(
                BlockRecord.id, BlockRecord.request_id, BlockRecord.direction, BlockRecord.position,
                BlockRecord.message_index, BlockRecord.block_type, BlockRecord.content_hash,
                BlockRecord.tool_name, BlockRecord.tool_call_id, BlockRecord.attrs,
                BlockRecord.source_key, BlockRecord.json_path, BlockContent.content,
            )
            # Content is only needed for tool calls (their arguments feed the source parsers).
            .outerjoin(BlockContent, and_(
                BlockRecord.content_hash == BlockContent.hash, BlockRecord.block_type == "tool_call",
            ))
            .where(BlockRecord.request_id.in_([r.id for r in requests]))
            .order_by(BlockRecord.request_id, BlockRecord.direction, BlockRecord.position)
        ).all()
        for row in block_rows:
            rows_by_request.setdefault(row.request_id, []).append(row)

        source_updates: list[dict] = []
        path_updates: list[dict] = []
        for request in requests:
            rows = rows_by_request.get(request.id, [])
            if not rows:
                stats["skipped_no_blocks"] += 1
                continue
            inputs = [r for r in rows if r.direction == "input"]
            outputs = [r for r in rows if r.direction == "output"]

            if request.classifier_version is None or request.classifier_version < CLASSIFIER_VERSION:
                attrs_by_row = []
                for row in inputs + outputs:
                    try:
                        attrs_by_row.append(json.loads(row.attrs) if row.attrs else {})
                    except json.JSONDecodeError:
                        attrs_by_row.append({})
                snapshots = [
                    BlockSnapshot(
                        direction=row.direction, block_type=row.block_type,
                        message_index=row.message_index, tool_name=row.tool_name,
                        tool_call_id=row.tool_call_id, attrs=attrs, content=row.content,
                        content_hash=row.content_hash,
                    )
                    for row, attrs in zip(inputs + outputs, attrs_by_row)
                ]
                infos = resolve_sources(snapshots, agent=request.agent)
                for row, attrs, info in zip(inputs + outputs, attrs_by_row, infos):
                    row_update: dict = {"id": row.id, "source_key": info.key, "file_path": info.file_path}
                    if info.detail:
                        row_update["attrs"] = json.dumps({**attrs, "source": info.detail})
                    source_updates.append(row_update)
                result = derive_purpose(PurposeInputs(
                    agent=request.agent,
                    input_blocks=snapshots[:len(inputs)],
                    output_blocks=snapshots[len(inputs):],
                    # Only claim a response when the stored rows prove one; otherwise omit it.
                    has_response=bool(outputs) or bool(request.has_response_doc),
                ))
                db.execute(
                    update(Request).where(Request.id == request.id).values(
                        purpose=result.purpose,
                        purpose_detail=json.dumps(result.detail) if result.detail else None,
                        classifier_version=CLASSIFIER_VERSION,
                    )
                )
                stats["classified"] += 1

            for direction, stored, has_doc in (
                ("input", inputs, request.has_request_doc),
                ("output", outputs, request.has_response_doc),
            ):
                if not (json_paths and has_doc and stored and any(r.json_path is None for r in stored)):
                    continue
                adapter = get_adapter(request.endpoint)
                if adapter is None:
                    continue
                try:
                    column = Request.canonical_request_body if direction == "input" else Request.canonical_response_body
                    document = json.loads(db.execute(select(column).where(Request.id == request.id)).scalar_one())
                    parsed = (
                        adapter.parse_request(document)[0] if direction == "input"
                        else adapter.parse_response(document)[0]
                    )
                except Exception:
                    stats["path_failed"] += 1
                    logger.debug("%s: could not re-parse %s %s document", label, request.id, direction, exc_info=True)
                    continue
                matched = _v9_match_json_paths(stored, parsed)
                if not matched:
                    stats["path_mismatch"] += 1
                path_updates.extend({"id": row_id, "json_path": path} for row_id, path in matched.items())

        if source_updates:
            db.execute(update(BlockRecord), source_updates)
            stats["source_keys"] += len(source_updates)
        if path_updates:
            db.execute(update(BlockRecord), path_updates)
            stats["paths_set"] += len(path_updates)
        db.flush()
        db.expire_all()  # keep the identity map from growing across batches

        previous = done
        done += len(requests)
        if done // _V9_PROGRESS_EVERY != previous // _V9_PROGRESS_EVERY:
            _report(f"{label}: {done:,}/{total:,} requests processed")

    db.info[f"{label}_backfill"] = stats
    _report(
        f"{label}: done. classified {stats['classified']:,} requests, wrote {stats['source_keys']:,} source keys "
        f"and {stats['paths_set']:,} JSON paths ({stats['path_mismatch']:,} directions did not match "
        f"their retained document, {stats['path_failed']:,} could not be re-parsed)"
    )


_DATA_MIGRATIONS: dict[int, Callable[[OrmSession], None]] = {
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
    8: _migrate_to_v8,
    9: _migrate_to_v9,
    10: _migrate_to_v10,
}
