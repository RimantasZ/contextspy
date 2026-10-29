"""Revisioned, bounded read service for derived session-lineage graphs.

The store remains authoritative. Nothing in this module writes lineage or conversation
membership to the database. Callers supply the snapshot repository function so their
transaction owns the complete read and existing CRUD entry points stay compatible.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import weakref
from collections import OrderedDict
from functools import lru_cache
from threading import Lock
from typing import Callable, NamedTuple

from sqlalchemy import String, cast, event, func, literal_column, or_, select
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.lineage import ANALYSIS_VERSION, build_lineage_graph
from contextspy.analysis.lineage_types import RequestSnapshot
from contextspy.db.models import BlockRecord, Request

CACHE_ENTRY_LIMIT = 4
CACHE_BYTES_LIMIT = 12 * 1024 * 1024  # serialized-size proxy, not heap size
_cache: OrderedDict[tuple, tuple[dict, int]] = OrderedDict()
_cache_bytes = 0
_cache_lock = Lock()

# The revision of a session, remembered between calls. ``evidence_revision`` reads every
# block row of the session (about 2 s for 4,000 requests) and used to run on every call,
# including the 5 s polls of the UI. A memo is dropped when a write to the evidence tables
# is executed or committed or rolled back on any connection of a watched engine (see
# ``invalidate``), when the request count of the session differs, and after
# REVISION_TTL_SECONDS, which bounds the staleness for writers outside this process.
REVISION_TTL_SECONDS = 60.0
REVISION_MEMO_LIMIT = 256


class _Memo(NamedTuple):
    revision: str
    count: int
    generation: int
    at: float


_revisions: OrderedDict[tuple, _Memo] = OrderedDict()
_generation = 0
_watched: "weakref.WeakSet" = weakref.WeakSet()
_EVIDENCE_WRITE = re.compile(
    r"^\s*(?:insert\s+(?:or\s+\w+\s+)?into|update|delete\s+from|replace\s+into)\s+\W?(?:requests|blocks)\b",
    re.IGNORECASE)
_DIRTY = "contextspy_lineage_dirty"


def _now() -> float:
    return time.monotonic()


def invalidate() -> None:
    """Forget every remembered revision (and any computation that is in flight).

    Called automatically for writes to ``requests`` and ``blocks`` through a watched engine;
    call it directly from code that changes lineage evidence another way. All sessions are
    dropped because the revision of one session reads requests of the others (external
    parents).
    """
    global _generation
    with _cache_lock:
        _generation += 1
        _revisions.clear()


@lru_cache(maxsize=512)
def _writes_evidence(statement: str) -> bool:
    return bool(_EVIDENCE_WRITE.match(statement))


def watch_engine(engine) -> None:
    """Attach the invalidation events to ``engine`` once.

    ``init_db`` does this when the engine is created, so no write goes unnoticed; the first
    read through any other engine does it as a fallback (a write already in progress at that
    moment would be missed).
    """
    if engine in _watched:
        return
    with _cache_lock:
        if engine in _watched:
            return
        _watched.add(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _statement(conn, _cursor, statement, _parameters, _context, _executemany):
        # Drop the memo now so a read in the same transaction sees its own write, and again
        # when the transaction ends, so a reader that computed meanwhile cannot keep a
        # revision of data that was never committed or has just been committed.
        if _writes_evidence(statement):
            conn.info[_DIRTY] = True
            invalidate()

    @event.listens_for(engine, "commit")
    @event.listens_for(engine, "rollback")
    def _finished(conn):
        if conn.info.pop(_DIRTY, False):
            invalidate()


_FIELD = literal_column("char(31)")  # unit separator: cannot appear in the joined values by accident
_HASH_PREFIX = 16
_NULL = literal_column("char(30)")  # distinguishes NULL from the empty string


def _block_fingerprints(db: OrmSession, scope):
    """One row per request in ``scope`` (a condition on ``Request``): block count and
    every block column the graph reads.

    The text is joined inside SQLite, so a long session costs one short row per request
    instead of one Python tuple per block. ``attrs`` and the capture-time annotation
    columns (``source_key``, ``json_path``, ``file_path``) are deliberately not read: the
    lineage snapshots do not load them either.
    """
    def text(column):
        return func.coalesce(cast(column, String), _NULL)

    columns = (
        BlockRecord.id, BlockRecord.direction, BlockRecord.position,
        BlockRecord.message_index, BlockRecord.block_type, BlockRecord.category,
        # A prefix of the digest is as good as the whole of it at detecting an edit and
        # keeps the per-block text (and so the transfer) about half as long.
        func.substr(BlockRecord.content_hash, 1, _HASH_PREFIX),
        BlockRecord.token_count, BlockRecord.tool_name, BlockRecord.tool_call_id,
    )
    block_text = text(columns[0])
    for column in columns[1:]:
        block_text = block_text + _FIELD + text(column)
    # The inner ORDER BY fixes the order in which group_concat sees the rows, and lets
    # SQLite stream them from idx_blocks_request instead of sorting.
    ordered = (
        select(BlockRecord.request_id.label("request_id"), block_text.label("text"))
        .select_from(BlockRecord.__table__.join(Request.__table__, Request.id == BlockRecord.request_id))
        .where(scope)
        .order_by(BlockRecord.request_id, BlockRecord.id)
        .subquery()
    )
    return db.execute(
        select(ordered.c.request_id, func.count(), func.group_concat(ordered.c.text, "\n"))
        .group_by(ordered.c.request_id).order_by(ordered.c.request_id)
    )


def evidence_revision(db: OrmSession, session_id: str, session_count: int) -> str:
    """Hash relevant persisted metadata, not raw bodies or unrelated sessions.

    This catches in-place edits, deletes, reassignments, and external exact-parent
    changes. The check is proportional to evidence size, even on a cache hit, so block rows
    are fingerprinted per request inside SQLite (see ``_block_fingerprints``).
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

    def add_fingerprints(rows) -> None:
        # Large strings: feed them to the hash as they are instead of through repr().
        for request_id, count, text in rows:
            digest.update(f"{request_id}\x1f{count}\x1f".encode())
            digest.update(text.encode())
            digest.update(b"\n")

    internal_rows = db.execute(
        select(*evidence_columns).where(Request.session_id == session_id)
        .order_by(Request.id)
    ).all()
    add_rows(internal_rows)
    add_fingerprints(_block_fingerprints(db, Request.session_id == session_id))

    # External parents: only predecessors that no request of this session answers can have
    # one (the same rule as crud.get_session_lineage_snapshots). The loader matches by
    # (provider, response id), so the lookup is per provider; it still scans the requests
    # table on databases whose idx_requests_provider_response is on another column.
    answered = {
        (row.provider, row.provider_response_id)
        for row in internal_rows if row.provider_response_id
    }
    predecessors_by_provider: dict[str, set[str]] = {}
    for row in internal_rows:
        if row.predecessor_response_id and (row.provider, row.predecessor_response_id) not in answered:
            predecessors_by_provider.setdefault(row.provider, set()).add(
                row.predecessor_response_id)
    external_rows = sorted((
        row
        for provider, response_ids in predecessors_by_provider.items()
        for row in db.execute(
            select(*evidence_columns)
            .where(Request.provider == provider,
                   Request.provider_response_id.in_(response_ids),
                   or_(Request.session_id != session_id, Request.session_id.is_(None)))
        ).all()
    ), key=lambda row: row.id)
    add_rows(external_rows)
    if external_rows:  # few rows: an IN list is fine here, but slower than the join for a session
        add_fingerprints(_block_fingerprints(db, Request.id.in_([row.id for row in external_rows])))
    return f"{ANALYSIS_VERSION}:{session_count}:{digest.hexdigest()[:20]}"


def revision_for_session(db: OrmSession, session_id: str, session_count: int) -> str:
    """``evidence_revision``, remembered until a write could have changed it.

    The result equals ``evidence_revision`` for every write made through this process's
    engine; a write from another process is picked up when the request count changes or
    after REVISION_TTL_SECONDS.
    """
    engine = db.get_bind().engine
    watch_engine(engine)
    key = (engine, session_id)
    with _cache_lock:
        memo = _revisions.get(key)
        if (memo is not None and memo.generation == _generation and memo.count == session_count
                and _now() - memo.at < REVISION_TTL_SECONDS):
            return memo.revision
        generation = _generation
    started = _now()
    revision = evidence_revision(db, session_id, session_count)
    with _cache_lock:
        if _generation == generation:  # nothing was written while the fingerprint was read
            _revisions[key] = _Memo(revision, session_count, generation, started)
            _revisions.move_to_end(key)
            while len(_revisions) > REVISION_MEMO_LIMIT:
                _revisions.popitem(last=False)
    return revision


def graph_for_session(
    db: OrmSession, session_id: str, session_count: int,
    load_snapshots: Callable[[OrmSession, str], tuple[list[RequestSnapshot], list[RequestSnapshot]]],
) -> tuple[dict, str]:
    """Reuse one graph revision across dashboard, conversation, and diagnostic views."""
    global _cache_bytes
    revision = revision_for_session(db, session_id, session_count)
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
