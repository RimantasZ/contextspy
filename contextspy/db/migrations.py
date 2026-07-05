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

SCHEMA_VERSION = 2

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

    return json.loads(get_meta(db, _PENDING_KEY, "[]") or "[]")


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


_DATA_MIGRATIONS: dict[int, Callable[[OrmSession], None]] = {
    2: _migrate_to_v2,
}
