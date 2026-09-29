"""Revisioned, bounded read service for derived session-lineage graphs.

The store remains authoritative. Nothing in this module writes lineage or conversation
membership to the database. Callers supply the snapshot repository function so their
transaction owns the complete read and existing CRUD entry points stay compatible.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Lock
from typing import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.lineage import ANALYSIS_VERSION, build_lineage_graph
from contextspy.analysis.lineage_types import RequestSnapshot
from contextspy.db.models import BlockRecord, Request

CACHE_ENTRY_LIMIT = 4
CACHE_BYTES_LIMIT = 12 * 1024 * 1024  # serialized-size proxy, not heap size
_cache: OrderedDict[tuple, tuple[dict, int]] = OrderedDict()
_cache_bytes = 0
_cache_lock = Lock()


def evidence_revision(db: OrmSession, session_id: str, session_count: int) -> str:
    """Hash relevant persisted metadata, not raw bodies or unrelated sessions.

    This catches in-place edits, deletes, reassignments, and external exact-parent
    changes. The check is proportional to evidence size, even on a cache hit.
    """
    evidence_columns = (
        Request.id, Request.session_id, Request.session_seq, Request.timestamp,
        Request.started_at, Request.duration_ms, Request.provider, Request.model,
        Request.agent, Request.endpoint, Request.provider_response_id,
        Request.predecessor_response_id, Request.context_fidelity,
        Request.tokens_total_input, Request.tokens_total_output,
        Request.provider_input_tokens, Request.stream_hint_source,
        Request.stream_hint_digest,
    )
    digest = hashlib.sha256()

    def add_rows(rows) -> None:
        for row in rows:
            digest.update(repr(tuple(row)).encode())
            digest.update(b"\n")

    internal_rows = db.execute(
        select(*evidence_columns).where(Request.session_id == session_id)
        .order_by(Request.id)
    ).all()
    add_rows(internal_rows)
    internal_ids = {row.id for row in internal_rows}
    predecessor_ids = {
        row.predecessor_response_id for row in internal_rows
        if row.predecessor_response_id
    }
    external_rows = db.execute(
        select(*evidence_columns)
        .where(Request.provider_response_id.in_(predecessor_ids),
               or_(Request.session_id != session_id, Request.session_id.is_(None)))
        .order_by(Request.id)
    ).all() if predecessor_ids else []
    add_rows(external_rows)
    request_ids = internal_ids | {row.id for row in external_rows}
    if request_ids:
        add_rows(db.execute(
            select(
                BlockRecord.id, BlockRecord.request_id, BlockRecord.direction,
                BlockRecord.position, BlockRecord.message_index,
                BlockRecord.block_type, BlockRecord.category,
                BlockRecord.content_hash, BlockRecord.token_count,
                BlockRecord.tool_name, BlockRecord.tool_call_id, BlockRecord.attrs,
            ).where(BlockRecord.request_id.in_(request_ids))
            .order_by(BlockRecord.request_id, BlockRecord.id)
        ))
    return f"{ANALYSIS_VERSION}:{session_count}:{digest.hexdigest()[:20]}"


def graph_for_session(
    db: OrmSession, session_id: str, session_count: int,
    load_snapshots: Callable[[OrmSession, str], tuple[list[RequestSnapshot], list[RequestSnapshot]]],
) -> tuple[dict, str]:
    """Reuse one graph revision across dashboard, conversation, and diagnostic views."""
    global _cache_bytes
    revision = evidence_revision(db, session_id, session_count)
    key = (db.get_bind(), session_id, revision)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached[0], revision

    snapshots, external = load_snapshots(db, session_id)
    graph = build_lineage_graph(snapshots, external_requests=external)
    graph_size = len(json.dumps(graph, separators=(",", ":")))
    with _cache_lock:
        # One revision per session; an oversized graph is served but not retained.
        for old_key in list(_cache):
            if old_key[:2] == key[:2]:
                _cache_bytes -= _cache.pop(old_key)[1]
        if graph_size <= CACHE_BYTES_LIMIT:
            _cache[key] = (graph, graph_size)
            _cache_bytes += graph_size
            while len(_cache) > CACHE_ENTRY_LIMIT or _cache_bytes > CACHE_BYTES_LIMIT:
                _, (_, evicted_size) = _cache.popitem(last=False)
                _cache_bytes -= evicted_size
    return graph, revision
