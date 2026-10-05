"""DB side of "where does this block occur": scope selection and row loading.

The aggregation itself is ``analysis/block_occurrences.py``. Conversation membership comes from the
existing lineage analysis (``crud.get_session_lineage_graph`` and the helpers behind the ``C<n>`` request
codes); nothing here defines membership itself.

Building or even revalidating the lineage graph costs seconds on long sessions (the revision check
hashes every block row), which is too slow to pay on every block selection. The membership of one session is
therefore kept for ``MEMBERSHIP_TTL_SECONDS`` and invalidated immediately when the session's request count changes,
so a live session is at most a minute behind on in-place lineage edits and never behind on new requests.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.activity import activity_for
from contextspy.analysis.block_occurrences import OccurrenceIndex, OccurrenceRow, ScopeRequest
from contextspy.db import crud
from contextspy.db.models import BlockRecord, Request

MEMBERSHIP_TTL_SECONDS = 60
_MEMBERSHIP_CACHE_LIMIT = 8


class _Membership(NamedTuple):
    groups: dict[str, frozenset[str]]       # conversation key -> request ids that belong to it
    filed_under: dict[str, str | None]      # request id -> key of the conversation it is filed under
    codes: dict[str, str]                   # request id -> "C<n>" / "AUX"
    auxiliary: frozenset[str]


_cache: dict[tuple, tuple[float, _Membership]] = {}
_cache_lock = Lock()


def clear_membership_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _membership(db: OrmSession, session_id: str, request_count: int) -> _Membership:
    key = (db.get_bind(), session_id, request_count)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    graph = crud.get_session_lineage_graph(db, session_id)
    auxiliary = frozenset((graph.get("auxiliary") or {}).get("request_ids", []))
    code_for = crud._conversation_code_resolver(graph)
    nodes = {node["request_id"]: node for node in graph["nodes"] if not node["external"]}
    membership = _Membership(
        groups={
            group["key"]: frozenset(rid for rid in group["request_ids"] if rid in nodes)
            for group in graph["conversations"]
        },
        filed_under={rid: crud._node_group_key(node, auxiliary) for rid, node in nodes.items()},
        codes={rid: code_for(node) for rid, node in nodes.items()},
        auxiliary=auxiliary,
    )
    with _cache_lock:
        for old in [k for k in _cache if k[:2] == key[:2]]:
            del _cache[old]  # one entry per session
        while len(_cache) >= _MEMBERSHIP_CACHE_LIMIT:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]
        _cache[key] = (now + MEMBERSHIP_TTL_SECONDS, membership)
    return membership


def _scope_requests(db: OrmSession, request: Request, scope: str) -> tuple[list[ScopeRequest], str, str | None]:
    """The ordered requests of the scope, the scope actually used, and a reason when it differs from the one asked for."""
    if request.session_id is None:
        only = ScopeRequest(request.id, request.session_seq, None, request.context_fidelity)
        return [only], "request", "no_session"

    rows = db.execute(
        select(Request.id, Request.session_seq, Request.timestamp, Request.context_fidelity)
        .where(Request.session_id == request.session_id)
    ).all()
    # Same order as the dashboard's request sequence (session_seq, completion time, id).
    ordered = sorted(rows, key=lambda r: (r.session_seq if r.session_seq is not None else -1, r.timestamp, r.id))

    note: str | None = None
    effective = "session"
    members: frozenset[str] | None = None
    codes: dict[str, str] = {}
    if scope == "conversation":
        membership = _membership(db, request.session_id, len(rows))
        key = membership.filed_under.get(request.id)
        if request.id in membership.auxiliary:
            note = "auxiliary_request"
        elif key is None or key not in membership.groups:
            note = "conversation_unavailable"
        else:
            effective, members, codes = "conversation", membership.groups[key], membership.codes
    return [
        ScopeRequest(row.id, row.session_seq, codes.get(row.id), row.context_fidelity)
        for row in ordered if members is None or row.id in members
    ], effective, note


def _index(db: OrmSession, request_id: str, block_id: int, scope: str):
    request = db.get(Request, request_id)
    block = db.get(BlockRecord, block_id)
    if request is None or block is None or block.request_id != request_id:
        return None
    scope_requests, effective, note = _scope_requests(db, request, scope)

    if block.direction != "input" or block.content_hash is None:
        # No cross-request identity (output block, or content that is hidden/empty): just this block.
        rows = [OccurrenceRow(request_id, block.id, block.token_count)]
        kind = "none"
    else:
        query = (
            select(BlockRecord.id, BlockRecord.request_id, BlockRecord.token_count)
            .where(BlockRecord.content_hash == block.content_hash, BlockRecord.direction == "input")
        )
        if request.session_id is None:
            query = query.where(BlockRecord.request_id == request.id)
        else:
            query = query.join(Request, Request.id == BlockRecord.request_id).where(
                Request.session_id == request.session_id
            )
        rows = [OccurrenceRow(r.request_id, r.id, r.token_count) for r in db.execute(query).all()]
        kind = "content_hash"

    index = OccurrenceIndex(scope_requests, rows, current_request_id=request_id, current_block_id=block_id)
    identity = {
        "kind": kind,
        "block_type": block.block_type,
        "tool_name": block.tool_name,
        "source_key": block.source_key,
        "activity": activity_for(block.source_key),
    }
    return index, identity, effective, note


def get_block_occurrences(db: OrmSession, request_id: str, block_id: int, scope: str) -> dict | None:
    """Summary of where the block occurs; None when the request/block pair does not exist."""
    found = _index(db, request_id, block_id, scope)
    if found is None:
        return None
    index, identity, effective, note = found
    return {
        "scope": effective,
        "requested_scope": scope,
        "scope_note": note,
        "identity": identity,
        "ranges": index.runs(),
        "requests_sample": index.sample(),
        "totals": index.totals(),
    }


def list_block_occurrence_requests(
    db: OrmSession, request_id: str, block_id: int, scope: str,
    from_position: int, to_position: int, limit: int,
) -> dict | None:
    found = _index(db, request_id, block_id, scope)
    if found is None:
        return None
    index, _identity, effective, _note = found
    entries, has_more = index.entries(from_position, to_position, limit)
    return {"scope": effective, "requests": entries, "has_more": has_more}


__all__ = ["get_block_occurrences", "list_block_occurrence_requests", "clear_membership_cache"]
