"""Revisioned, bounded read service for derived session-lineage graphs.

The store remains authoritative. Nothing in this module writes lineage or conversation
membership to the database. Callers supply the snapshot repository function so their
transaction owns the complete read and existing CRUD entry points stay compatible.

A live session gets a new capture every few seconds, and each one changes its revision. So
that the next poll does not redo the whole session, the evidence is kept per request:

- The revision combines one digest per request (and one for the external parents), so a
  capture that only appended rows hashes just the new requests (``_advance``). Any other
  write to the evidence tables, a rolled back transaction, a count that does not add up, or
  a base older than ``REVISION_TTL_SECONDS`` falls back to the full computation, which gives
  the same revision for the same data (``evidence_revision``).
- A request's lineage snapshot is reused while its digest is unchanged, so only new or
  changed requests have their block rows loaded again, and ``LineageBuildMemo`` reuses the
  per-request and per-pair parts of the graph build. The graph itself is always rebuilt
  over all requests: its block weights depend on every request of the session.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Any, Callable, NamedTuple

from sqlalchemy import String, cast, event, func, literal_column, or_, select
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.lineage import ANALYSIS_VERSION, LineageBuildMemo, build_lineage_graph
from contextspy.analysis.lineage_types import RequestSnapshot
from contextspy.db.models import BlockRecord, Request

CACHE_ENTRY_LIMIT = 4
CACHE_BYTES_LIMIT = 12 * 1024 * 1024  # serialized-size proxy, not heap size
_cache: OrderedDict[tuple, tuple[dict, int]] = OrderedDict()
_cache_bytes = 0
_cache_lock = Lock()

# The revision of a session, remembered between calls. A memo is dropped when a write to the
# evidence tables is executed or committed or rolled back on any connection of a watched
# engine (see ``invalidate``), when the request count of the session differs, and after
# REVISION_TTL_SECONDS, which bounds the staleness for writers outside this process.
REVISION_TTL_SECONDS = 60.0
REVISION_MEMO_LIMIT = 256
# Per-request evidence (a row and a digest per request) is kept for EVIDENCE_SESSION_LIMIT
# sessions; their lineage snapshots, which hold every block row, only for the
# SNAPSHOT_SESSION_LIMIT most recently built ones.
EVIDENCE_SESSION_LIMIT = 16
SNAPSHOT_SESSION_LIMIT = 2


class _Memo(NamedTuple):
    revision: str
    count: int
    generation: int
    at: float


@dataclass
class _Evidence:
    """The inputs of one session's revision, as of the watermarks.

    ``request_mark`` and ``block_mark`` are the largest ``requests.rowid`` and ``blocks.id``
    when it was read. SQLite has one writer at a time and gives a new row the next rowid, so
    a later plain insert always lands above them.
    """
    rows: dict[str, tuple]          # internal request id -> evidence row
    digests: dict[str, int]         # internal request id -> digest of row and block rows
    total: int                      # sum of ``digests`` modulo 2**256
    request_mark: int
    block_mark: int
    rewrites: int                   # ``_rewrites`` when the base was computed
    at: float                       # when the base was fully computed
    snapshots: dict[str, tuple[int, RequestSnapshot]] = field(default_factory=dict)
    memo: LineageBuildMemo = field(default_factory=LineageBuildMemo)


_revisions: OrderedDict[tuple, _Memo] = OrderedDict()
_evidence: OrderedDict[tuple, _Evidence] = OrderedDict()
_generation = 0   # bumped by every write to the evidence tables
_rewrites = 0     # bumped by every such write that is not a plain append (and by invalidate)
_watched: "weakref.WeakSet" = weakref.WeakSet()
_EVIDENCE_WRITE = re.compile(
    r"^\s*(?:insert\s+(?:or\s+\w+\s+)?into|update|delete\s+from|replace\s+into)\s+\W?(?:requests|blocks)\b",
    re.IGNORECASE)
_PLAIN_APPEND = re.compile(
    r"^\s*insert\s+into\s+\W?(requests|blocks)\W?\s*\(([^)]*)\)\s*(?:values|select)\b",
    re.IGNORECASE)
_DIRTY = "contextspy_lineage_dirty"
_APPEND, _REWRITE = "append", "rewrite"
_DIGEST_MODULUS = 2 ** 256


def _now() -> float:
    return time.monotonic()


def _bump(*, rewrite: bool) -> None:
    global _generation, _rewrites
    with _cache_lock:
        _generation += 1
        if rewrite:
            _rewrites += 1
        _revisions.clear()


def invalidate() -> None:
    """Forget every remembered revision (and any computation that is in flight).

    Called automatically for writes to ``requests`` and ``blocks`` through a watched engine;
    call it directly from code that changes lineage evidence another way. All sessions are
    dropped because the revision of one session reads requests of the others (external
    parents). The next revision is computed in full.
    """
    _bump(rewrite=True)


@lru_cache(maxsize=512)
def _writes_evidence(statement: str) -> bool:
    return bool(_EVIDENCE_WRITE.match(statement))


@lru_cache(maxsize=512)
def _appends_only(statement: str) -> bool:
    """True for a plain ``INSERT INTO requests|blocks (...)`` that cannot touch existing rows.

    Upserts, ``OR REPLACE``/``OR IGNORE`` variants, inserts without a column list and inserts
    that choose their own row id (a block ``id`` could fill a gap below the watermark) count
    as rewrites.
    """
    match = _PLAIN_APPEND.match(statement)
    if match is None or re.search(r"\bon\s+conflict\b", statement, re.IGNORECASE):
        return False
    columns = {column.strip().strip('"`[]').lower() for column in match.group(2).split(",")}
    if columns & {"rowid", "_rowid_", "oid"}:
        return False
    return not (match.group(1).lower() == "blocks" and "id" in columns)


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
            kind = _APPEND if _appends_only(statement) else _REWRITE
            if conn.info.get(_DIRTY) != _REWRITE:
                conn.info[_DIRTY] = kind
            _bump(rewrite=kind == _REWRITE)

    @event.listens_for(engine, "commit")
    def _committed(conn):
        kind = conn.info.pop(_DIRTY, None)
        if kind is not None:
            _bump(rewrite=kind == _REWRITE)

    @event.listens_for(engine, "rollback")
    def _rolled_back(conn):
        # A reader inside the transaction may have seen the rows that are now gone.
        if conn.info.pop(_DIRTY, None) is not None:
            _bump(rewrite=True)

    @event.listens_for(engine, "rollback_savepoint")
    def _rolled_back_savepoint(conn, _name, _context):
        if conn.info.get(_DIRTY) is not None:
            conn.info[_DIRTY] = _REWRITE
            _bump(rewrite=True)


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


_EVIDENCE_COLUMNS = (
    Request.id, Request.session_id, Request.session_seq, Request.timestamp,
    Request.started_at, Request.duration_ms, Request.provider, Request.model,
    Request.agent, Request.endpoint, Request.provider_response_id,
    Request.predecessor_response_id, Request.context_fidelity,
    Request.tokens_total_input, Request.tokens_total_output,
    Request.provider_input_tokens, Request.stream_hint_source,
    Request.stream_hint_digest,
)
_ROWID = literal_column("requests.rowid")


def _request_digest(row: tuple, count: int, text: str) -> int:
    digest = hashlib.sha256(repr(tuple(row)).encode())
    digest.update(f"\n{count}\x1f".encode())
    digest.update(text.encode())
    return int.from_bytes(digest.digest(), "big")


def _marks(db: OrmSession) -> tuple[int, int]:
    """The watermarks, read (in one statement) before the rows they bound."""
    request_mark, block_mark = db.execute(select(
        select(func.max(_ROWID)).select_from(Request.__table__).scalar_subquery(),
        select(func.max(BlockRecord.id)).scalar_subquery(),
    )).one()
    return request_mark or 0, block_mark or 0


def _full_evidence(db: OrmSession, session_id: str) -> _Evidence:
    """Every request of the session and its block rows, hashed per request."""
    request_mark, block_mark = _marks(db)
    rows = {
        row.id: tuple(row) for row in db.execute(
            select(*_EVIDENCE_COLUMNS).where(Request.session_id == session_id)
        ).all()
    }
    fingerprints = {
        request_id: (count, text)
        for request_id, count, text in _block_fingerprints(db, Request.session_id == session_id)
    }
    digests = {
        request_id: _request_digest(row, *fingerprints.get(request_id, (0, "")))
        for request_id, row in rows.items()
    }
    return _Evidence(
        rows=rows, digests=digests, total=sum(digests.values()) % _DIGEST_MODULUS,
        request_mark=request_mark, block_mark=block_mark, rewrites=-1, at=0.0,
    )


def _advance(db: OrmSession, session_id: str, base: _Evidence) -> _Evidence:
    """``base`` plus the rows inserted since its watermarks (valid only if nothing else was
    written: the caller checks ``_rewrites``)."""
    request_mark, block_mark = _marks(db)
    rows = dict(base.rows)
    digests = dict(base.digests)
    added: set[str] = set()
    if request_mark > base.request_mark:
        for row in db.execute(
            select(*_EVIDENCE_COLUMNS).where(
                _ROWID > base.request_mark, _ROWID <= request_mark,
                Request.session_id == session_id,
            )
        ).all():
            rows[row.id] = tuple(row)
            added.add(row.id)
    fingerprints: dict[str, tuple[int, str]] = {}
    if added or block_mark > base.block_mark:
        # New requests, and requests of the session that got new block rows (one query).
        touched = (
            select(BlockRecord.request_id)
            .where(BlockRecord.id > base.block_mark, BlockRecord.id <= block_mark)
        )
        scope = Request.id.in_(touched)
        if added:
            scope = or_(Request.id.in_(sorted(added)), scope)
        fingerprints = {
            request_id: (count, text)
            for request_id, count, text in _block_fingerprints(
                db, (Request.session_id == session_id) & scope,
            )
            if request_id in rows
        }
    total = base.total
    for request_id in added | fingerprints.keys():
        digest = _request_digest(rows[request_id], *fingerprints.get(request_id, (0, "")))
        total = (total - digests.get(request_id, 0) + digest) % _DIGEST_MODULUS
        digests[request_id] = digest
    return _Evidence(
        rows=rows, digests=digests, total=total, request_mark=request_mark,
        block_mark=block_mark, rewrites=base.rewrites, at=base.at,
        snapshots=base.snapshots, memo=base.memo,
    )


def _external_digest(db: OrmSession, session_id: str, rows: dict[str, tuple]) -> bytes:
    """External parents: only predecessors that no request of this session answers can have
    one (the same rule as crud.get_session_lineage_snapshots). They are few, so they are read
    in full every time; any insert anywhere may have answered one."""
    index = {column.key: position for position, column in enumerate(_EVIDENCE_COLUMNS)}
    provider_at, response_at = index["provider"], index["provider_response_id"]
    predecessor_at = index["predecessor_response_id"]
    answered = {(row[provider_at], row[response_at]) for row in rows.values() if row[response_at]}
    predecessors_by_provider: dict[str, set[str]] = {}
    for row in rows.values():
        if row[predecessor_at] and (row[provider_at], row[predecessor_at]) not in answered:
            predecessors_by_provider.setdefault(row[provider_at], set()).add(row[predecessor_at])
    # The loader matches by (provider, response id), so the lookup is per provider; it still
    # scans the requests table on databases whose idx_requests_provider_response is on
    # another column.
    external_rows = sorted((
        row
        for provider, response_ids in predecessors_by_provider.items()
        for row in db.execute(
            select(*_EVIDENCE_COLUMNS)
            .where(Request.provider == provider,
                   Request.provider_response_id.in_(response_ids),
                   or_(Request.session_id != session_id, Request.session_id.is_(None)))
        ).all()
    ), key=lambda row: row.id)
    digest = hashlib.sha256()
    for row in external_rows:
        digest.update(repr(tuple(row)).encode())
        digest.update(b"\n")
    if external_rows:  # few rows: an IN list is fine here, but slower than the join for a session
        for request_id, count, text in _block_fingerprints(
            db, Request.id.in_([row.id for row in external_rows]),
        ):
            digest.update(f"{request_id}\x1f{count}\x1f".encode())
            digest.update(text.encode())
            digest.update(b"\n")
    return digest.digest()


def _revision(db: OrmSession, session_id: str, session_count: int, evidence: _Evidence) -> str:
    digest = hashlib.sha256(evidence.total.to_bytes(32, "big"))
    digest.update(_external_digest(db, session_id, evidence.rows))
    return f"{ANALYSIS_VERSION}:{session_count}:{digest.hexdigest()[:20]}"


def evidence_revision(db: OrmSession, session_id: str, session_count: int) -> str:
    """Hash relevant persisted metadata, not raw bodies or unrelated sessions.

    This catches in-place edits, deletes, reassignments, and external exact-parent
    changes. Block rows are fingerprinted per request inside SQLite (see
    ``_block_fingerprints``); the per-request digests are summed, so the revision does not
    depend on the order the rows are read in and an append can update it in place.
    """
    return _revision(db, session_id, session_count, _full_evidence(db, session_id))


def _remember_evidence(key: tuple, evidence: _Evidence) -> None:
    _evidence[key] = evidence
    _evidence.move_to_end(key)
    while len(_evidence) > EVIDENCE_SESSION_LIMIT:
        _evidence.popitem(last=False)


def revision_for_session(db: OrmSession, session_id: str, session_count: int) -> str:
    """``evidence_revision``, remembered until a write could have changed it.

    The result equals ``evidence_revision`` for every write made through this process's
    engine; a write from another process is picked up when the request count changes or
    after REVISION_TTL_SECONDS (an edit from another process that coincides with an append
    from this one is picked up after the TTL).
    """
    return _current(db, session_id, session_count)[0]


def _current(
    db: OrmSession, session_id: str, session_count: int,
) -> tuple[str, _Evidence | None, int]:
    """The revision, the evidence it was computed from, and the write generation as of
    which that evidence was read (the generation of the remembered computation on a hit)."""
    engine = db.get_bind().engine
    watch_engine(engine)
    key = (engine, session_id)
    with _cache_lock:
        memo = _revisions.get(key)
        if (memo is not None and memo.generation == _generation and memo.count == session_count
                and _now() - memo.at < REVISION_TTL_SECONDS):
            # A memo is only kept when nothing was written while it was computed, and every
            # write since drops it: the remembered evidence is that of this revision.
            return memo.revision, _evidence.get(key), memo.generation
        generation, rewrites = _generation, _rewrites
        base = _evidence.get(key)
    started = _now()
    evidence = None
    if base is not None and base.rewrites == rewrites and started - base.at < REVISION_TTL_SECONDS:
        evidence = _advance(db, session_id, base)
        if len(evidence.rows) != session_count:  # a delete or move we were not told about
            evidence = None
    if evidence is None:
        evidence = _full_evidence(db, session_id)
        evidence.rewrites, evidence.at = rewrites, started
        if base is not None:  # snapshots stay valid for every request whose digest matches
            evidence.snapshots, evidence.memo = base.snapshots, base.memo
    revision = _revision(db, session_id, session_count, evidence)
    with _cache_lock:
        if _rewrites == rewrites:  # appends meanwhile are above the watermarks: still a base
            _remember_evidence(key, evidence)
        if _generation == generation:  # nothing was written while the fingerprint was read
            _revisions[key] = _Memo(revision, session_count, generation, started)
            _revisions.move_to_end(key)
            while len(_revisions) > REVISION_MEMO_LIMIT:
                _revisions.popitem(last=False)
    return revision, evidence, generation


def _keep_snapshots(key: tuple, evidence: _Evidence, snapshots: list[RequestSnapshot]) -> None:
    evidence.snapshots = {
        snapshot.id: (evidence.digests[snapshot.id], snapshot)
        for snapshot in snapshots if snapshot.id in evidence.digests
    }
    holding = [other for other, value in _evidence.items() if value.snapshots and other != key]
    for other in holding[:max(0, len(holding) - (SNAPSHOT_SESSION_LIMIT - 1))]:
        _evidence[other].snapshots = {}
        _evidence[other].memo = LineageBuildMemo()


def graph_for_session(
    db: OrmSession, session_id: str, session_count: int,
    load_snapshots: Callable[..., tuple[list[RequestSnapshot], list[RequestSnapshot]]],
) -> tuple[dict, str]:
    """Reuse one graph revision across dashboard, conversation, and diagnostic views.

    ``load_snapshots(db, session_id[, reusable])`` reads the snapshots; ``reusable`` holds
    the snapshots of requests whose digest in this revision's evidence is the one they were
    read with, so they equal what reading them again would give.
    """
    global _cache_bytes
    revision, evidence, generation = _current(db, session_id, session_count)
    key = (db.get_bind(), session_id, revision)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached[0], revision

    reusable: dict[str, RequestSnapshot] = {}
    memo = LineageBuildMemo()
    if evidence is not None:
        memo = evidence.memo
        reusable = {
            request_id: snapshot
            for request_id, (digest, snapshot) in evidence.snapshots.items()
            if evidence.digests.get(request_id) == digest
        }
    if reusable:
        snapshots, external = load_snapshots(db, session_id, reusable)
    else:
        snapshots, external = load_snapshots(db, session_id)
    graph = build_lineage_graph(snapshots, external_requests=external, memo=memo)
    memo.retain([*snapshots, *external])
    graph_size = len(json.dumps(graph, separators=(",", ":")))
    with _cache_lock:
        # Each snapshot is tagged with its digest in this evidence. That is only right if
        # nothing was written between reading the evidence and reading the snapshots.
        evidence_key = (db.get_bind().engine, session_id)
        if evidence is not None and _generation == generation and _evidence.get(evidence_key) is evidence:
            _keep_snapshots(evidence_key, evidence, snapshots)
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
