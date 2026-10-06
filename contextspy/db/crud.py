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
from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as OrmSession, load_only

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.db import session_lineage_service
from contextspy.db.models import BlockContent, BlockRecord, Request, Session, ToolStat

if TYPE_CHECKING:
    from contextspy.analysis.blocks import Block


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(db: OrmSession, name: str) -> Session:
    session = Session(
        id=str(uuid.uuid4()),
        name=name,
        started_at=datetime.now(timezone.utc),
        is_active=1,
    )
    db.add(session)
    db.flush()
    return session


def get_active_session(db: OrmSession) -> Session | None:
    return db.execute(
        select(Session).where(Session.is_active == 1)
    ).scalars().first()


def get_session(db: OrmSession, session_id: str) -> Session | None:
    return db.get(Session, session_id)


def list_sessions(db: OrmSession) -> list[Session]:
    return list(
        db.execute(select(Session).order_by(Session.started_at.desc())).scalars().all()
    )


def end_session(db: OrmSession, session_id: str) -> Session | None:
    session = db.get(Session, session_id)
    if session:
        session.ended_at = datetime.now(timezone.utc)
        session.is_active = 0
        db.flush()
    return session


def rename_session(db: OrmSession, session_id: str, new_name: str) -> Session | None:
    session = db.get(Session, session_id)
    if session:
        session.name = new_name
        db.flush()
    return session


def delete_session(db: OrmSession, session_id: str) -> bool:
    session = db.get(Session, session_id)
    if not session:
        return False
    # Disassociate requests first
    db.execute(
        text("UPDATE requests SET session_id = NULL WHERE session_id = :sid"),
        {"sid": session_id},
    )
    db.delete(session)
    db.flush()
    return True


def delete_session_with_requests(db: OrmSession, session_id: str) -> bool:
    """Delete session and all requests (+ cascaded tool_stats) that belong to it."""
    session = db.get(Session, session_id)
    if not session:
        return False
    db.execute(
        text("DELETE FROM requests WHERE session_id = :sid"),
        {"sid": session_id},
    )
    db.delete(session)
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def _next_session_seq(db: OrmSession, session_id: str | None) -> int | None:
    if session_id is None:
        return None
    # Increment and return in one SQLite write statement so concurrent proxy
    # workers cannot both observe the same MAX(session_seq). The MAX fallback
    # also makes upgraded databases safe before their v5 data migration has
    # initialized next_request_seq from historical rows.
    next_value = db.execute(
        text("""
            UPDATE sessions
            SET next_request_seq = MAX(
                next_request_seq,
                (
                    SELECT COALESCE(MAX(session_seq), 0) + 1
                    FROM requests
                    WHERE session_id = :session_id
                )
            ) + 1
            WHERE id = :session_id
            RETURNING next_request_seq
        """),
        {"session_id": session_id},
    ).scalar_one_or_none()
    return next_value - 1 if next_value is not None else None


def create_request(db: OrmSession, data: dict[str, Any]) -> Request:
    data = dict(data)
    data.setdefault("session_seq", _next_session_seq(db, data.get("session_id")))
    req = Request(**data)
    db.add(req)
    db.flush()
    return req


def get_request(db: OrmSession, request_id: str) -> Request | None:
    return db.get(Request, request_id)


def get_request_by_provider_response_id(
    db: OrmSession, provider: str, response_id: str,
) -> Request | None:
    """Resolve explicit provider lineage without relying on row adjacency."""
    return db.execute(
        select(Request)
        .where(
            Request.provider == provider,
            Request.provider_response_id == response_id,
        )
        .order_by(Request.timestamp.desc())
    ).scalars().first()


def get_unique_request_by_provider_response_id(
    db: OrmSession, provider: str, response_id: str,
) -> Request | None:
    """Resolve state only if the provider ID identifies exactly one stored row."""
    rows = db.execute(
        select(Request)
        .where(
            Request.provider == provider,
            Request.provider_response_id == response_id,
        )
        .limit(2)
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None


def _lineage_snapshots_for_requests(
    db: OrmSession, requests: list[Request],
) -> list:
    """Materialize provider-neutral lineage snapshots with one bulk block query."""
    from contextspy.analysis.context_diff import ContextBlock
    from contextspy.analysis.lineage import RequestSnapshot

    request_ids = [request.id for request in requests]
    blocks_by_request: dict[str, list[ContextBlock]] = {
        request_id: [] for request_id in request_ids
    }
    if request_ids:
        rows = db.execute(
            select(BlockRecord)
            .options(load_only(
                BlockRecord.id, BlockRecord.request_id, BlockRecord.direction,
                BlockRecord.position, BlockRecord.message_index,
                BlockRecord.block_type, BlockRecord.category,
                BlockRecord.content_hash, BlockRecord.token_count,
                BlockRecord.tool_name, BlockRecord.tool_call_id,
                raiseload=True,
            ))
            .where(BlockRecord.request_id.in_(request_ids))
            .order_by(
                BlockRecord.request_id.asc(),
                BlockRecord.direction.asc(),
                BlockRecord.position.asc(),
                BlockRecord.id.asc(),
            )
        ).scalars().all()
        for block in rows:
            blocks_by_request[block.request_id].append(ContextBlock(
                id=block.id,
                request_id=block.request_id,
                direction=block.direction,
                position=block.position,
                message_index=block.message_index,
                block_type=block.block_type,
                category=block.category,
                content_hash=block.content_hash,
                token_count=block.token_count,
                tool_name=block.tool_name,
                tool_call_id=block.tool_call_id,
            ))

    return [RequestSnapshot(
        id=request.id,
        session_id=request.session_id,
        session_seq=request.session_seq,
        timestamp=request.timestamp,
        started_at=request.started_at,
        duration_ms=request.duration_ms,
        provider=request.provider,
        model=request.model,
        agent=request.agent,
        endpoint=request.endpoint,
        provider_response_id=request.provider_response_id,
        predecessor_response_id=request.predecessor_response_id,
        context_fidelity=request.context_fidelity,
        tokens_total_input=request.tokens_total_input,
        tokens_total_output=request.tokens_total_output,
        stream_hint_source=request.stream_hint_source,
        stream_hint_digest=request.stream_hint_digest,
        blocks=tuple(blocks_by_request.get(request.id, ())),
        external=False,
    ) for request in requests]


def get_session_lineage_snapshots(db: OrmSession, session_id: str) -> tuple[list, list]:
    """Load one capture and any exact parents outside it without N+1 queries."""
    from dataclasses import replace

    lineage_columns = (
        Request.id, Request.session_id, Request.session_seq, Request.timestamp,
        Request.started_at, Request.duration_ms, Request.provider, Request.model,
        Request.agent, Request.endpoint, Request.provider_response_id,
        Request.predecessor_response_id, Request.context_fidelity,
        Request.tokens_total_input, Request.tokens_total_output,
        Request.stream_hint_source, Request.stream_hint_digest,
    )
    requests = list(db.execute(
        select(Request)
        .options(load_only(*lineage_columns, raiseload=True))
        .where(Request.session_id == session_id)
        .order_by(Request.session_seq.asc(), Request.timestamp.asc(), Request.id.asc())
    ).scalars().all())
    internal = _lineage_snapshots_for_requests(db, requests)

    internal_response_ids = {
        (request.provider, request.provider_response_id)
        for request in requests if request.provider_response_id
    }
    missing_ids = {
        (request.provider, request.predecessor_response_id)
        for request in requests
        if request.predecessor_response_id
        and (request.provider, request.predecessor_response_id) not in internal_response_ids
    }
    if not missing_ids:
        return internal, []

    response_ids = {response_id for _, response_id in missing_ids}
    candidates = list(db.execute(
        select(Request)
        .options(load_only(*lineage_columns, raiseload=True))
        .where(Request.provider_response_id.in_(response_ids))
        .order_by(Request.timestamp.desc(), Request.id.desc())
    ).scalars().all())
    selected: dict[tuple[str, str], Request] = {}
    for request in candidates:
        key = (request.provider, request.provider_response_id or "")
        if key in missing_ids:
            selected.setdefault(key, request)
    external = [
        replace(snapshot, external=True)
        for snapshot in _lineage_snapshots_for_requests(db, list(selected.values()))
    ]
    return internal, external


def get_context_diff_snapshots(
    db: OrmSession, parent_id: str, child_id: str,
) -> tuple | None:
    rows = list(db.execute(
        select(Request).where(Request.id.in_((parent_id, child_id)))
    ).scalars().all())
    if len(rows) != 2:
        return None
    snapshots = {
        snapshot.id: snapshot for snapshot in _lineage_snapshots_for_requests(db, rows)
    }
    return snapshots[parent_id], snapshots[child_id]


_SORT_COLUMNS = {
    'timestamp': Request.timestamp,
    'tokens_total_input': Request.tokens_total_input,
    'tokens_total_output': Request.tokens_total_output,
    'duration_ms': Request.duration_ms,
    'status_code': Request.status_code,
    'provider': Request.provider,
    'agent': Request.agent,
    'model': Request.model,
}


def list_requests(
    db: OrmSession,
    session_id: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    q: str | None = None,
    status_category: str | None = None,
    purpose: str | None = None,
    sort_by: str = 'timestamp',
    sort_dir: str = 'desc',
    limit: int = 50,
    offset: int = 0,
) -> list[Request]:
    stmt = select(Request)
    if sort_by == 'session':
        stmt = stmt.outerjoin(Session, Request.session_id == Session.id)
    if session_id is not None:
        stmt = stmt.where(Request.session_id == session_id)
    if provider:
        stmt = stmt.where(Request.provider == provider)
    if agent:
        # Stats expose missing agent metadata as "unknown". Keep the request
        # filter aligned with that value while also accepting a literal value.
        if agent == "unknown":
            stmt = stmt.where(or_(Request.agent.is_(None), Request.agent == agent))
        else:
            stmt = stmt.where(Request.agent == agent)
    if model:
        stmt = stmt.where(Request.model == model)
    if purpose:
        # Requests that were never classified read as "unknown", like the agent filter.
        if purpose == "unknown":
            stmt = stmt.where(or_(Request.purpose.is_(None), Request.purpose == purpose))
        else:
            stmt = stmt.where(Request.purpose == purpose)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Request.model.ilike(like),
                Request.agent.ilike(like),
                Request.endpoint.ilike(like),
                Request.provider.ilike(like),
            )
        )
    if status_category == "success":
        stmt = stmt.where(or_(
            Request.invocation_outcome == "completed",
            (Request.status_code >= 200) & (Request.status_code < 300),
        ))
    elif status_category == "error":
        stmt = stmt.where(or_(
            Request.invocation_outcome == "failed",
            Request.status_code >= 400,
        ))
    col = Session.name if sort_by == 'session' else _SORT_COLUMNS.get(sort_by, Request.timestamp)
    stmt = stmt.order_by(col.asc() if sort_dir == 'asc' else col.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_vals: list[int], p: float) -> int | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[lo]
    return round(sorted_vals[lo] + (k - lo) * (sorted_vals[hi] - sorted_vals[lo]))


_CATEGORY_COLS = [
    "tokens_system_prompt",
    "tokens_tool_definitions",
    "tokens_tool_results",
    "tokens_file_contents",
    "tokens_conversation_history",
    "tokens_current_user_message",
    "tokens_assistant_prefill",
    "tokens_uncategorized",
]


def get_stats(db: OrmSession, session_id: str | None = None) -> dict:
    q = select(Request)
    if session_id is not None:
        q = q.where(Request.session_id == session_id)
    rows = list(db.execute(q).scalars().all())

    if not rows:
        return _empty_stats()

    total_input = sum(r.tokens_total_input for r in rows)
    total_output = sum(r.tokens_total_output for r in rows)
    output_text = sum(r.tokens_output_text for r in rows)
    output_thinking = sum(r.tokens_output_thinking for r in rows)

    # Provider-reported cache usage (cache_read_tokens/cache_creation_tokens are
    # None for requests whose provider doesn't report cache usage at all — those
    # are excluded rather than treated as 0%, since 0% is itself a meaningful
    # value for a provider that does report but had no cache hit). Two views are
    # kept because they answer different questions: avg_pct treats every request
    # equally (a 10k-token request at 50% cached counts the same as a 100k-token
    # request at 95%), while overall_pct is token-weighted, so a session
    # dominated by one huge highly-cached request isn't diluted by many small
    # uncached ones.
    cache_rows = [
        r for r in rows
        if r.provider_input_tokens and (r.cache_read_tokens is not None or r.cache_creation_tokens is not None)
    ]
    if cache_rows:
        cache_pct_values = [
            ((r.cache_read_tokens or 0) + (r.cache_creation_tokens or 0)) / r.provider_input_tokens * 100
            for r in cache_rows
        ]
        total_cache_tokens = sum((r.cache_read_tokens or 0) + (r.cache_creation_tokens or 0) for r in cache_rows)
        total_cache_input = sum(r.provider_input_tokens for r in cache_rows)
        cache = {
            "avg_pct": round(sum(cache_pct_values) / len(cache_pct_values), 1),
            "overall_pct": round(total_cache_tokens / total_cache_input * 100, 1) if total_cache_input else None,
            "reporting_request_count": len(cache_rows),
        }
    else:
        cache = {"avg_pct": None, "overall_pct": None, "reporting_request_count": 0}

    by_category: dict[str, dict] = {}
    for col in _CATEGORY_COLS:
        cat_key = col[len("tokens_"):]
        total_cat = sum(getattr(r, col) for r in rows)
        pct = round(total_cat / total_input * 100, 1) if total_input else 0.0
        by_category[cat_key] = {"tokens": total_cat, "pct": pct}

    # by_provider
    by_provider: dict[str, int] = {}
    for r in rows:
        by_provider[r.provider] = by_provider.get(r.provider, 0) + 1

    # by_agent
    by_agent: dict[str, int] = {}
    for r in rows:
        key = r.agent or "unknown"
        by_agent[key] = by_agent.get(key, 0) + 1

    # by_model
    by_model: dict[str, int] = {}
    for r in rows:
        key = r.model or "unknown"
        by_model[key] = by_model.get(key, 0) + 1

    # latency percentiles
    latency_vals = sorted(r.duration_ms for r in rows if r.duration_ms is not None)
    latency = {
        "avg_ms": round(sum(latency_vals) / len(latency_vals)) if latency_vals else None,
        "p50_ms": _percentile(latency_vals, 50),
        "p95_ms": _percentile(latency_vals, 95),
        "p99_ms": _percentile(latency_vals, 99),
        "min_ms": latency_vals[0] if latency_vals else None,
        "max_ms": latency_vals[-1] if latency_vals else None,
    }

    # Preserve exact HTTP codes when present, otherwise use the
    # transport-neutral provider invocation outcome.
    by_status: dict[str, int] = {}
    for r in rows:
        key = str(r.status_code) if r.status_code is not None else r.invocation_outcome
        by_status[key] = by_status.get(key, 0) + 1

    error_count = sum(
        1 for r in rows
        if r.invocation_outcome == "failed"
        or (r.status_code is not None and r.status_code >= 400)
    )
    unknown_status_count = sum(
        1 for r in rows
        if r.status_code is None and r.invocation_outcome == "unknown"
    )

    # session timing derived from request timestamps
    timestamps = [r.timestamp for r in rows]
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    session_timing = {
        "first_request_at": first_ts.isoformat(),
        "last_request_at": last_ts.isoformat(),
        "elapsed_ms": int((last_ts - first_ts).total_seconds() * 1000),
        "active_duration_ms": sum(r.duration_ms for r in rows if r.duration_ms is not None),
    }

    return {
        "request_count": len(rows),
        "tokens_total_input": total_input,
        "tokens_total_output": total_output,
        "tokens_output_text": output_text,
        "tokens_output_thinking": output_thinking,
        "cache": cache,
        "by_category": by_category,
        "by_provider": by_provider,
        "by_agent": by_agent,
        "by_model": by_model,
        "latency": latency,
        "by_status": by_status,
        "error_count": error_count,
        "unknown_status_count": unknown_status_count,
        "session_timing": session_timing,
    }


def _empty_stats() -> dict:
    _empty_latency = {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "min_ms": None, "max_ms": None}
    _empty_timing = {"first_request_at": None, "last_request_at": None, "elapsed_ms": None, "active_duration_ms": None}
    return {
        "request_count": 0,
        "tokens_total_input": 0,
        "tokens_total_output": 0,
        "tokens_output_text": 0,
        "tokens_output_thinking": 0,
        "cache": {"avg_pct": None, "overall_pct": None, "reporting_request_count": 0},
        "by_category": {
            col[len("tokens_"):]: {"tokens": 0, "pct": 0.0}
            for col in _CATEGORY_COLS
        },
        "by_provider": {},
        "by_agent": {},
        "by_model": {},
        "latency": _empty_latency,
        "by_status": {},
        "error_count": 0,
        "unknown_status_count": 0,
        "session_timing": _empty_timing,
    }


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def insert_blocks(db: OrmSession, request_id: str, blocks: list["Block"]) -> None:
    """Persist a request's analyzed blocks, content-addressed.

    Each unique content string is written once to block_contents
    (INSERT OR IGNORE on the hash); the block row itself — type, category,
    token_count, tool attribution — is always written, one row per block.
    """
    position_by_direction: dict[str, int] = {}
    for b in blocks:
        pos = position_by_direction.get(b.direction, 0)
        position_by_direction[b.direction] = pos + 1

        if b.content_hash and b.content:
            stmt = sqlite_insert(BlockContent).values(
                hash=b.content_hash, content=b.content, created_at=datetime.now(timezone.utc),
            ).on_conflict_do_nothing(index_elements=["hash"])
            db.execute(stmt)

        db.add(BlockRecord(
            request_id=request_id,
            direction=b.direction,
            position=pos,
            message_index=b.message_index,
            block_type=b.block_type,
            category=b.category,
            content_hash=b.content_hash,
            token_count=b.token_count,
            tool_name=b.tool_name,
            tool_call_id=b.tool_call_id,
            attrs=json.dumps(b.attrs) if b.attrs else None,
            source_key=b.source_key,
            json_path=(
                json.dumps(list(b.json_path), separators=(",", ":"))
                if b.json_path is not None else None
            ),
        ))
    db.flush()


def get_blocks(db: OrmSession, request_id: str) -> list[dict]:
    rows = db.execute(
        select(BlockRecord, BlockContent.content)
        .outerjoin(BlockContent, BlockRecord.content_hash == BlockContent.hash)
        .where(BlockRecord.request_id == request_id)
        .order_by(BlockRecord.direction.asc(), BlockRecord.position.asc())
    ).all()

    # First-seen tracking: content is deduped by hash (see insert_blocks), so the
    # same system prompt / growing conversation turn recurs verbatim across many
    # requests in a session. For each hash in this request, find the earliest
    # session_seq (within the same session) at which it appeared — lets the UI
    # show when a piece of context first entered the window rather than just that
    # it's present now.
    session_id = db.execute(
        select(Request.session_id).where(Request.id == request_id)
    ).scalar_one_or_none()

    first_seen: dict[str, int] = {}
    if session_id is not None:
        hashes = {r.content_hash for r, _ in rows if r.content_hash is not None}
        if hashes:
            first_seen = dict(db.execute(
                select(BlockRecord.content_hash, func.min(Request.session_seq))
                .join(Request, BlockRecord.request_id == Request.id)
                .where(Request.session_id == session_id, BlockRecord.content_hash.in_(hashes))
                .group_by(BlockRecord.content_hash)
            ).all())

    # Resolve tool_call <-> tool_definition and tool_result <-> tool_call/tool_definition
    # links from the join keys already on each block (tool_name, tool_call_id) — no
    # extra query, and no stored FK needed since both sides of each link always live
    # in the same request's block set (the agent resends full history each turn).
    definition_by_name = {
        r.tool_name: r.id for r, _ in rows if r.block_type == BlockType.TOOL_DEFINITION and r.tool_name
    }
    call_by_id = {
        r.tool_call_id: r.id for r, _ in rows if r.block_type == BlockType.TOOL_CALL and r.tool_call_id
    }

    # Conversation traceback: chain user/assistant message blocks by message_index so a
    # block can link back to the previous conversational turn, skipping over tool-only
    # turns (calls/results/system/tool-defs) in between. Canonical id per message_index
    # is the first block encountered there (lowest position) — matters when one message
    # has multiple text parts sharing the same message_index.
    _MESSAGE_TYPES = (BlockType.USER_MESSAGE, BlockType.ASSISTANT_MESSAGE)
    message_blocks = sorted(
        (r for r, _ in rows
         if r.direction == Direction.INPUT and r.block_type in _MESSAGE_TYPES and r.message_index is not None),
        key=lambda r: (r.message_index, r.position),
    )
    first_by_index: dict[int, int] = {}
    for r in message_blocks:
        first_by_index.setdefault(r.message_index, r.id)

    prev_message_id_by_index: dict[int, int | None] = {}
    prev_id: int | None = None
    for idx in sorted(first_by_index):
        prev_message_id_by_index[idx] = prev_id
        prev_id = first_by_index[idx]

    result = []
    for record, content in rows:
        content_purged = record.content_hash is not None and content is None
        linked_call_id = None
        linked_definition_id = None
        linked_previous_message_id = None
        if record.block_type == BlockType.TOOL_CALL:
            linked_definition_id = definition_by_name.get(record.tool_name)
        elif record.block_type == BlockType.TOOL_RESULT:
            linked_call_id = call_by_id.get(record.tool_call_id)
            linked_definition_id = definition_by_name.get(record.tool_name)
        elif record.direction == Direction.INPUT and record.block_type in _MESSAGE_TYPES:
            linked_previous_message_id = prev_message_id_by_index.get(record.message_index)
        result.append(record.to_dict(
            content=content,
            content_purged=content_purged,
            linked_call_id=linked_call_id,
            linked_definition_id=linked_definition_id,
            linked_previous_message_id=linked_previous_message_id,
            first_seen_session_seq=first_seen.get(record.content_hash) if record.content_hash else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Tool stats
# ---------------------------------------------------------------------------

def upsert_tool_stats(db: OrmSession, request_id: str, tool_rows: list[dict]) -> None:
    """Insert per-tool token counts for a request."""
    for row in tool_rows:
        stat = ToolStat(
            request_id=request_id,
            tool_name=row["tool_name"],
            definition_tokens=row.get("definition_tokens", 0),
            result_tokens=row.get("result_tokens", 0),
        )
        db.add(stat)
    db.flush()


def get_tool_stats(
    db: OrmSession,
    session_id: str | None = None,
    request_id: str | None = None,
) -> list[dict]:
    """Aggregate definition_tokens and result_tokens per tool_name."""
    q = select(
        ToolStat.tool_name,
        func.sum(ToolStat.definition_tokens).label("definition_tokens"),
        func.sum(ToolStat.result_tokens).label("result_tokens"),
    )
    if request_id is not None:
        q = q.where(ToolStat.request_id == request_id)
    elif session_id is not None:
        q = q.join(Request, ToolStat.request_id == Request.id).where(
            Request.session_id == session_id
        )
    q = q.group_by(ToolStat.tool_name).order_by(
        func.sum(ToolStat.definition_tokens).desc()
    )
    rows = db.execute(q).all()
    return [
        {
            "tool_name": r.tool_name,
            "definition_tokens": r.definition_tokens or 0,
            "result_tokens": r.result_tokens or 0,
        }
        for r in rows
    ]


def get_timeline(
    db: OrmSession,
    session_id: str | None = None,
    bucket: str = "hour",
) -> list[dict]:
    bucket_map = {"minute": "%Y-%m-%dT%H:%M", "hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d"}
    fmt = bucket_map.get(bucket, "%Y-%m-%dT%H")

    q = select(Request)
    if session_id is not None:
        q = q.where(Request.session_id == session_id)

    rows = list(db.execute(q).scalars().all())
    buckets: dict[str, dict] = {}
    for r in rows:
        key = r.timestamp.strftime(fmt)
        if key not in buckets:
            buckets[key] = {"bucket": key, "request_count": 0, "tokens_total_input": 0}
        buckets[key]["request_count"] += 1
        buckets[key]["tokens_total_input"] += r.tokens_total_input

    return sorted(buckets.values(), key=lambda x: x["bucket"])


# ---------------------------------------------------------------------------
# Sessions summary (for dashboard timeline table)
# ---------------------------------------------------------------------------

def get_sessions_summary(db: OrmSession) -> list[dict]:
    """
    Return a combined timeline of sessions and no-session gap periods,
    ordered newest-first.  Each entry has:
      type, session_id, name, started_at, ended_at, is_active,
      request_count, tokens_in, tokens_out
    """
    # All sessions, oldest-first for gap detection
    sessions = list(
        db.execute(select(Session).order_by(Session.started_at.asc())).scalars().all()
    )

    # Per-session request stats in one aggregation query
    session_stats_rows = db.execute(
        select(
            Request.session_id,
            func.count().label("req_count"),
            func.sum(Request.tokens_total_input).label("tok_in"),
            func.sum(Request.tokens_total_output).label("tok_out"),
            func.sum(Request.tokens_system_prompt).label("tok_system_prompt"),
            func.sum(Request.tokens_tool_definitions).label("tok_tool_definitions"),
            func.sum(Request.tokens_tool_results).label("tok_tool_results"),
            func.sum(Request.tokens_file_contents).label("tok_file_contents"),
            func.sum(Request.tokens_conversation_history).label("tok_conversation_history"),
            func.sum(Request.tokens_current_user_message).label("tok_current_user_message"),
            func.sum(Request.tokens_assistant_prefill).label("tok_assistant_prefill"),
            func.sum(Request.tokens_uncategorized).label("tok_uncategorized"),
        )
        .where(Request.session_id.isnot(None))
        .group_by(Request.session_id)
    ).all()
    session_stats: dict[str, dict] = {
        row.session_id: {
            "req_count": row.req_count,
            "tok_in": row.tok_in or 0,
            "tok_out": row.tok_out or 0,
            "tokens_system_prompt": row.tok_system_prompt or 0,
            "tokens_tool_definitions": row.tok_tool_definitions or 0,
            "tokens_tool_results": row.tok_tool_results or 0,
            "tokens_file_contents": row.tok_file_contents or 0,
            "tokens_conversation_history": row.tok_conversation_history or 0,
            "tokens_current_user_message": row.tok_current_user_message or 0,
            "tokens_assistant_prefill": row.tok_assistant_prefill or 0,
            "tokens_uncategorized": row.tok_uncategorized or 0,
        }
        for row in session_stats_rows
    }

    # All null-session requests, oldest-first
    null_req_rows = db.execute(
        select(
            Request.timestamp,
            Request.tokens_total_input,
            Request.tokens_total_output,
            Request.tokens_system_prompt,
            Request.tokens_tool_definitions,
            Request.tokens_tool_results,
            Request.tokens_file_contents,
            Request.tokens_conversation_history,
            Request.tokens_current_user_message,
            Request.tokens_assistant_prefill,
            Request.tokens_uncategorized,
        )
        .where(Request.session_id.is_(None))
        .order_by(Request.timestamp.asc())
    ).all()

    # Build gap windows: each window is (start_boundary, end_boundary)
    # where None means "no bound" (i.e. −∞ or +∞)
    windows: list[tuple] = []
    if not sessions:
        windows.append((None, None))
    else:
        windows.append((None, sessions[0].started_at))
        for i in range(len(sessions) - 1):
            windows.append((sessions[i].ended_at, sessions[i + 1].started_at))
        last = sessions[-1]
        if not last.is_active:
            windows.append((last.ended_at, None))

    entries: list[dict] = []

    for win_start, win_end in windows:
        reqs = [
            r for r in null_req_rows
            if (win_start is None or r.timestamp >= win_start)
            and (win_end is None or r.timestamp < win_end)
        ]
        if not reqs:
            continue
        gap_start, gap_end = reqs[0].timestamp, reqs[-1].timestamp
        entries.append({
            "type": "gap",
            "session_id": None,
            "name": None,
            "started_at": gap_start.isoformat(),
            "ended_at": gap_end.isoformat(),
            "duration_ms": int((gap_end - gap_start).total_seconds() * 1000),
            "is_active": False,
            "request_count": len(reqs),
            "tokens_in": sum(r.tokens_total_input for r in reqs),
            "tokens_out": sum(r.tokens_total_output for r in reqs),
            "tokens_system_prompt": sum(r.tokens_system_prompt for r in reqs),
            "tokens_tool_definitions": sum(r.tokens_tool_definitions for r in reqs),
            "tokens_tool_results": sum(r.tokens_tool_results for r in reqs),
            "tokens_file_contents": sum(r.tokens_file_contents for r in reqs),
            "tokens_conversation_history": sum(r.tokens_conversation_history for r in reqs),
            "tokens_current_user_message": sum(r.tokens_current_user_message for r in reqs),
            "tokens_assistant_prefill": sum(r.tokens_assistant_prefill for r in reqs),
            "tokens_uncategorized": sum(r.tokens_uncategorized for r in reqs),
        })

    # Session entries
    _empty: dict = {
        "req_count": 0, "tok_in": 0, "tok_out": 0,
        "tokens_system_prompt": 0, "tokens_tool_definitions": 0,
        "tokens_tool_results": 0, "tokens_file_contents": 0,
        "tokens_conversation_history": 0, "tokens_current_user_message": 0,
        "tokens_assistant_prefill": 0, "tokens_uncategorized": 0,
    }
    for s in sessions:
        stats = session_stats.get(s.id, _empty)
        duration_ms = (
            int((s.ended_at - s.started_at).total_seconds() * 1000)
            if s.ended_at else None
        )
        entries.append({
            "type": "session",
            "session_id": s.id,
            "name": s.name,
            "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "duration_ms": duration_ms,
            "is_active": bool(s.is_active),
            "status": s.status,
            "archived_at": s.archived_at.isoformat() if s.archived_at else None,
            "request_count": stats["req_count"],
            "tokens_in": stats["tok_in"],
            "tokens_out": stats["tok_out"],
            "tokens_system_prompt": stats["tokens_system_prompt"],
            "tokens_tool_definitions": stats["tokens_tool_definitions"],
            "tokens_tool_results": stats["tokens_tool_results"],
            "tokens_file_contents": stats["tokens_file_contents"],
            "tokens_conversation_history": stats["tokens_conversation_history"],
            "tokens_current_user_message": stats["tokens_current_user_message"],
            "tokens_assistant_prefill": stats["tokens_assistant_prefill"],
            "tokens_uncategorized": stats["tokens_uncategorized"],
        })

    entries.sort(key=lambda e: e["started_at"], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Live dashboard (active session)
# ---------------------------------------------------------------------------

_LIVE_ACTIVITY_LIMIT = 10
_CONVERSATION_PREVIEW_LIMIT = 15
_DISPLAY_REQUEST_COLUMNS = (
    Request.id, Request.session_id, Request.session_seq, Request.timestamp,
    Request.model, Request.duration_ms, Request.status_code,
    Request.invocation_outcome, Request.context_fidelity,
    Request.tokens_total_input, Request.tokens_total_output,
    Request.provider_input_tokens, Request.purpose,
)


def _display_requests():
    """Request metadata needed by cards, activity and context comparisons."""
    return select(Request).options(load_only(*_DISPLAY_REQUEST_COLUMNS, raiseload=True))


def _session_lineage_revision(db: OrmSession, session_id: str, session_count: int) -> str:
    """Compatibility entry point for the dedicated lineage read service."""
    return session_lineage_service.evidence_revision(db, session_id, session_count)


def _session_lineage_graph(db: OrmSession, session_id: str, session_count: int) -> tuple[dict, str]:
    return session_lineage_service.graph_for_session(
        db, session_id, session_count, get_session_lineage_snapshots,
    )


def get_session_lineage_graph(db: OrmSession, session_id: str) -> dict:
    """Return the same revisioned analysis used by dashboard projections."""
    count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
    return _session_lineage_graph(db, session_id, count)[0]


def get_session_lineage_revision(db: OrmSession, session_id: str) -> str:
    count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
    return _session_lineage_revision(db, session_id, count)


def _block_counts(
    db: OrmSession, request_ids: list[str],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    opaque = func.coalesce(func.json_extract(BlockRecord.attrs, "$.opaque"), 0)
    rows = db.execute(
        select(
            BlockRecord.request_id,
            BlockRecord.block_type,
            opaque.label("opaque"),
            func.count().label("block_count"),
        )
        .where(
            BlockRecord.request_id.in_(request_ids),
            BlockRecord.direction == Direction.INPUT,
        )
        .group_by(BlockRecord.request_id, BlockRecord.block_type, opaque)
    ).all()
    counts: dict[str, dict[str, int]] = {rid: {} for rid in request_ids}
    opaque_counts: dict[str, int] = {rid: 0 for rid in request_ids}
    for row in rows:
        if row.opaque:
            opaque_counts[row.request_id] += row.block_count
        else:
            counts[row.request_id][row.block_type] = row.block_count
    return counts, opaque_counts


def _comparison_fidelity(latest: Request, previous: Request) -> str:
    pair = {latest.context_fidelity, previous.context_fidelity}
    if "opaque" in pair:
        return "observed_only"
    if "partial" in pair:
        return "partial"
    return "complete"


def _context_change(
    latest: Request, parent: Request | None, *, parent_state: str,
    confidence: float | None, block_counts: dict[str, dict[str, int]],
    opaque_counts: dict[str, int],
    first_conversation: bool = False,
) -> dict:
    change: dict[str, Any] = {
        "request_id": latest.id,
        "session_seq": latest.session_seq,
        "tokens_total_input": latest.tokens_total_input,
        "provider_input_tokens": latest.provider_input_tokens,
        "parent_request_id": parent.id if parent else None,
        "parent_session_seq": parent.session_seq if parent else None,
        "parent_state": parent_state,
        "parent_confidence": confidence,
        "external_parent": bool(parent and parent.session_id != latest.session_id),
        "first_conversation": first_conversation,
        "token_delta": None,
        "comparison_fidelity": "unavailable",
        "block_changes": [],
        "opaque_changes": None,
    }
    if parent is None:
        return change

    change["token_delta"] = latest.tokens_total_input - parent.tokens_total_input
    fidelity = _comparison_fidelity(latest, parent)
    change["comparison_fidelity"] = fidelity
    if fidelity == "observed_only":
        change["opaque_changes"] = abs(
            opaque_counts.get(latest.id, 0) - opaque_counts.get(parent.id, 0)
        )

    current = block_counts.get(latest.id, {})
    prior = block_counts.get(parent.id, {})
    change["block_changes"] = [
        {
            "block_type": block_type,
            "current_count": current.get(block_type, 0),
            "previous_count": prior.get(block_type, 0),
            "delta": current.get(block_type, 0) - prior.get(block_type, 0),
        }
        for block_type in sorted(set(current) | set(prior))
    ]
    return change


def _flow_item(r: Request) -> dict:
    return {
        "id": r.id, "session_seq": r.session_seq, "timestamp": r.timestamp.isoformat(),
        "model": r.model, "duration_ms": r.duration_ms,
        "status_code": r.status_code, "invocation_outcome": r.invocation_outcome,
        "tokens_total_input": r.tokens_total_input,
        "tokens_total_output": r.tokens_total_output,
        "purpose": r.purpose,
    }


def _lineage_flow_item(row: Request, node: dict, edge: dict | None,
                       stream_bridges: dict, auxiliary_ids: set[str],
                       conversation_code: str) -> dict:
    rid = row.id
    return {
        **_flow_item(row),
        "conversation_code": conversation_code,
        "parent_request_id": edge["source_request_id"] if edge else None,
        "parent_state": node["parent_state"],
        "certainty": edge["certainty"] if edge else None,
        "confidence": edge["confidence"] if edge else None,
        "lineage_relation": (
            stream_bridges[rid]["evidence"] if rid in stream_bridges else
            "external" if edge and edge["external_source"] else
            edge["certainty"] if edge else node["parent_state"]
        ),
        "membership_state": (
            "provisional_unassigned" if rid in auxiliary_ids else
            "confirmed" if node["conversation_membership"] else "unassigned"
        ),
        "shared_history": len(node["conversation_membership"]) > 1,
        "fork_status": node["conversation_fork_status"],
    }


def _sequence_order(node: dict) -> tuple:
    return (node["session_seq"] if node["session_seq"] is not None else -1,
            node["completed_at"], node["request_id"])


def _node_group_key(node: dict, auxiliary_ids: set[str]) -> str | None:
    """Key of the conversation a request card is filed under (None for auxiliary).

    A request shown under several conversations takes its confirmed one first,
    then its first membership.
    """
    if node["request_id"] in auxiliary_ids:
        return None
    memberships = node["conversation_membership"]
    return next((item["key"] for item in memberships if item["state"] == "confirmed"),
                memberships[0]["key"] if memberships else None)


def _conversation_code_resolver(graph: dict):
    """Return ``node -> "C<n>" | "AUX"``, the label shown on request cards."""
    auxiliary_ids = set((graph.get("auxiliary") or {}).get("request_ids", []))
    codes = {group["key"]: f"C{index}" for index, group in enumerate(graph["conversations"], 1)}

    def code_for(node: dict) -> str:
        key = _node_group_key(node, auxiliary_ids)
        return codes.get(key, "AUX") if key is not None else "AUX"

    return code_for


def annotated_lineage_nodes(graph: dict) -> list[dict]:
    """Copies of the graph's nodes with presentation facts for the request detail page.

    - ``conversation_code``: ``C<n>`` / ``AUX`` as on request cards (None for external requests).
    - ``conversation_previous_request_id`` / ``conversation_next_request_id``: the
      neighbouring requests in the same conversation, in session order. Used by the UI as
      a labelled fallback when no direct parent/child edge was established. They are
      neighbours, not proven links, and are None for auxiliary and external requests.

    The graph itself is cached and shared, so it is never mutated here.
    """
    code_for = _conversation_code_resolver(graph)
    auxiliary_ids = set((graph.get("auxiliary") or {}).get("request_ids", []))
    by_id = {node["request_id"]: node for node in graph["nodes"]}

    def order(rid: str) -> tuple:
        node = by_id[rid]
        return (node["session_seq"] if node["session_seq"] is not None else -1,
                node["completed_at"], rid)

    ordered_by_group = {
        group["key"]: sorted((rid for rid in group["request_ids"]
                              if rid in by_id and not by_id[rid]["external"]), key=order)
        for group in graph["conversations"]
    }
    neighbours: dict[str, tuple[str | None, str | None]] = {}
    for key, ids in ordered_by_group.items():
        for index, rid in enumerate(ids):
            node = by_id[rid]
            if _node_group_key(node, auxiliary_ids) != key:
                continue  # filed under another conversation; use that one's neighbours
            neighbours[rid] = (ids[index - 1] if index > 0 else None,
                               ids[index + 1] if index + 1 < len(ids) else None)
    result = []
    for node in graph["nodes"]:
        previous_id, next_id = neighbours.get(node["request_id"], (None, None))
        result.append({
            **node,
            "conversation_code": None if node["external"] else code_for(node),
            "conversation_previous_request_id": previous_id,
            "conversation_next_request_id": next_id,
        })
    return result


def _session_sequence_projection(db: OrmSession, graph: dict, *, limit: int,
                                 cursor: str | None = None) -> dict:
    nodes = [node for node in graph["nodes"] if not node["external"]]
    ordered = sorted(nodes, key=_sequence_order, reverse=True)
    after = _cursor_decode(cursor) if cursor else None
    if after is not None and after not in {_sequence_order(node) for node in ordered}:
        raise ValueError("Request cursor does not belong to this session")
    eligible = [node for node in ordered if after is None or _sequence_order(node) < after]
    selected = eligible[:limit]
    rows = db.execute(_display_requests().where(Request.id.in_(
        node["request_id"] for node in selected
    ))).scalars().all() if selected else []
    row_by_id = {row.id: row for row in rows}
    edges = {edge["target_request_id"]: edge for edge in graph["edges"]
             if edge["relation_type"] == "context_continuation"}
    auxiliary_ids = set((graph.get("auxiliary") or {}).get("request_ids", []))
    code_for = _conversation_code_resolver(graph)

    return {
        "request_flow": [
            _lineage_flow_item(row_by_id[node["request_id"]], node,
                               edges.get(node["request_id"]),
                               graph.get("stream_bridges", {}), auxiliary_ids,
                               code_for(node))
            for node in selected
        ],
        "request_count": len(ordered),
        "next_cursor": (_cursor_encode(_sequence_order(selected[-1]))
                        if len(eligible) > limit and selected else None),
    }


def _session_activity(db: OrmSession, session_id: str) -> list[dict]:
    recent = db.execute(
        _display_requests().where(Request.session_id == session_id)
        .order_by(Request.session_seq.desc(), Request.timestamp.desc(), Request.id.desc())
        .limit(_LIVE_ACTIVITY_LIMIT)
    ).scalars().all()
    return [{"id": row.id, "session_seq": row.session_seq,
             "timestamp": row.timestamp.isoformat(),
             "tokens_total_input": row.tokens_total_input,
             "tokens_total_output": row.tokens_total_output}
            for row in reversed(recent)]


def _selected_context_change(db: OrmSession, graph: dict, request_id: str) -> dict:
    nodes = {node["request_id"]: node for node in graph["nodes"]}
    node = nodes.get(request_id)
    if node is None or node["external"]:
        raise KeyError("Request not found in session")
    edge = next((item for item in graph["edges"]
                 if item["target_request_id"] == request_id and
                 item["relation_type"] == "context_continuation"), None)
    parent_id = edge["source_request_id"] if edge else None
    ids = [request_id] + ([parent_id] if parent_id else [])
    rows = db.execute(_display_requests().where(Request.id.in_(ids))).scalars().all()
    by_id = {row.id: row for row in rows}
    block_counts, opaque_counts = _block_counts(db, ids)
    return _context_change(
        by_id[request_id], by_id.get(parent_id), parent_state=node["parent_state"],
        confidence=edge["confidence"] if edge else None,
        block_counts=block_counts, opaque_counts=opaque_counts,
        first_conversation=any(
            request_id in group["request_ids"] and group["evidence"] == "parallel_chains"
            for group in graph["conversations"]
        ) and edge is None,
    )


def get_session_sequence(db: OrmSession, session_id: str, *, revision: str | None = None,
                         cursor: str | None = None, limit: int = 15) -> dict:
    with db.begin_nested():
        if not get_session(db, session_id):
            raise KeyError("Session not found")
        count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
        graph, current_revision = _session_lineage_graph(db, session_id, count)
        if revision is not None and revision != current_revision:
            raise ValueError("Sequence revision changed; refresh and try again")
        page = _session_sequence_projection(db, graph, limit=limit, cursor=cursor)
        return {"session_id": session_id, "revision": current_revision,
                **page, "activity": _session_activity(db, session_id)}


def get_session_request_context(db: OrmSession, session_id: str, request_id: str,
                                *, revision: str | None = None) -> dict:
    with db.begin_nested():
        if not get_session(db, session_id):
            raise KeyError("Session not found")
        count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
        graph, current_revision = _session_lineage_graph(db, session_id, count)
        if revision is not None and revision != current_revision:
            raise ValueError("Sequence revision changed; refresh and try again")
        return {"session_id": session_id, "revision": current_revision,
                "context_change": _selected_context_change(db, graph, request_id)}


def _conversation_order(graph: dict) -> list[dict]:
    nodes = {node["request_id"]: node for node in graph["nodes"]}
    def order(rid: str) -> tuple:
        node = nodes[rid]
        return (node["completed_at"],
                node["session_seq"] if node["session_seq"] is not None else -1, rid)
    groups = graph["conversations"]
    return sorted(groups, key=lambda group: (
        max(map(order, group["request_ids"])), group["key"]), reverse=True)


def _cursor_encode(order: tuple) -> str:
    return base64.urlsafe_b64encode(json.dumps(order, separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_decode(cursor: str) -> tuple:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if (not isinstance(values, list) or len(values) != 3 or
                not isinstance(values[0], int) or not isinstance(values[1], str) or
                not isinstance(values[2], str)):
            raise ValueError
        return tuple(values)
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("Invalid request cursor") from exc


def _conversation_projection_view(
    db: OrmSession, graph: dict, groups: list[dict], *,
    preview_limit: int = _CONVERSATION_PREVIEW_LIMIT,
    only_group: str | None = None, cursor: str | None = None,
    request_limit: int | None = None,
) -> tuple[list[dict], dict | None]:
    """Shared dashboard/session presentation facts from one lineage result.

    All segment and membership decisions are made here, never in React. Rows and
    block counts are hydrated in batches after graph-wide classification.
    """
    nodes = {node["request_id"]: node for node in graph["nodes"]}
    conversation_codes = {group["key"]: f"C{index}" for index, group in enumerate(graph["conversations"], 1)}
    stream_bridges = graph.get("stream_bridges", {})
    parent_edges = {edge["target_request_id"]: edge for edge in graph["edges"]
                    if edge["relation_type"] == "context_continuation"}
    def order(rid: str) -> tuple:
        node = nodes[rid]
        return (node["session_seq"] if node["session_seq"] is not None else -1,
                node["completed_at"], rid)

    prepared = []
    visible_ids: set[str] = set()
    latest_ids: set[str] = set()
    for group in groups:
        all_ids = group["request_ids"]
        group_ids = set(all_ids)
        ordered = sorted(all_ids, key=order, reverse=True)
        segment_by_id: dict[str, str] = {}
        starts: dict[str, dict] = {}
        # Depth order guarantees an accepted parent has an assigned segment.
        for rid in sorted(all_ids, key=lambda item: (nodes[item]["depth"], order(item))):
            node = nodes[rid]
            edge = parent_edges.get(rid)
            parent_id = edge["source_request_id"] if edge else None
            parent_node = nodes.get(parent_id) if parent_id in group_ids else None
            if parent_node and parent_node["branch"] == node["branch"]:
                segment_by_id[rid] = segment_by_id[parent_id]
            else:
                segment_by_id[rid] = rid
                if parent_node:
                    gap = ("fork_branch" if parent_node["conversation_fork_status"] == "confirmed"
                           else "graph_branch_unconfirmed")
                elif edge and edge["external_source"]:
                    gap = "external"
                elif rid in stream_bridges:
                    gap = "stream_resume_unlinked"
                else:
                    gap = None if node["parent_state"] in ("exact", "inferred") else node["parent_state"]
                starts[rid] = {"key": rid, "gap_reason": gap, "first_request_id": rid,
                               "first_session_seq": node["session_seq"],
                               "latest_session_seq": node["session_seq"], "request_count": 0}
                if rid in stream_bridges:
                    starts[rid]["bridge_request_id"] = stream_bridges[rid]["prior_request_id"]
                    starts[rid]["bridge_evidence"] = stream_bridges[rid]["evidence"]
        for rid in ordered:
            segment = starts[segment_by_id[rid]]
            segment["request_count"] += 1
            if "newest_request_id" not in segment:
                segment["newest_request_id"] = rid
                segment["latest_session_seq"] = nodes[rid]["session_seq"]

        if request_limit is not None:
            after = _cursor_decode(cursor) if cursor else None
            if after is not None and after not in {order(rid) for rid in ordered}:
                raise ValueError("Request cursor does not belong to this conversation")
            eligible = [rid for rid in ordered if after is None or order(rid) < after]
            selected = eligible[:request_limit]
            has_more = len(eligible) > request_limit
        else:
            selected = ordered[:preview_limit]
            has_more = len(ordered) > preview_limit
        visible_ids.update(selected)
        latest_ids.add(ordered[0])
        prepared.append((group, ordered, selected, has_more, segment_by_id, starts))

    comparison_ids = set(latest_ids)
    for rid in latest_ids:
        edge = parent_edges.get(rid)
        if edge:
            comparison_ids.add(edge["source_request_id"])
    row_ids = set(visible_ids) | comparison_ids
    rows = list(db.execute(_display_requests().where(Request.id.in_(row_ids))).scalars().all()) if row_ids else []
    row_by_id = {row.id: row for row in rows}
    block_counts, opaque_counts = _block_counts(db, list(comparison_ids)) if comparison_ids else ({}, {})
    views = []
    page = None
    for group, ordered, selected, has_more, segment_by_id, starts in prepared:
        confirmed = set(group["confirmed_request_ids"])
        segment_slices: list[dict] = []
        for rid in selected:
            segment_key = segment_by_id[rid]
            if not segment_slices or segment_slices[-1]["key"] != segment_key:
                segment_slices.append({**starts[segment_key], "request_flow": []})
            edge = parent_edges.get(rid)
            node = nodes[rid]
            segment_slices[-1]["request_flow"].append({
                **_flow_item(row_by_id[rid]),
                "conversation_code": conversation_codes.get(group["key"], "AUX"),
                "parent_request_id": edge["source_request_id"] if edge else None,
                "parent_state": node["parent_state"],
                "certainty": edge["certainty"] if edge else None,
                "confidence": edge["confidence"] if edge else None,
                "lineage_relation": (
                    stream_bridges[rid]["evidence"] if rid in stream_bridges else
                    "external" if edge and edge["external_source"] else
                    edge["certainty"] if edge else node["parent_state"]
                ),
                "membership_state": (
                    "provisional_unassigned" if group["evidence"] == "auxiliary"
                    else "confirmed" if rid in confirmed else "unassigned"
                ),
                "shared_history": len(node["conversation_membership"]) > 1,
                "fork_status": node["conversation_fork_status"],
            })
        for segment in segment_slices:
            segment["segment_start_visible"] = any(
                card["id"] == segment["first_request_id"] for card in segment["request_flow"]
            )
            if not segment["segment_start_visible"]:
                segment["gap_reason"] = None
        latest_id = ordered[0]
        edge = parent_edges.get(latest_id)
        parent_id = edge["source_request_id"] if edge else None
        change = _context_change(
            row_by_id[latest_id], row_by_id.get(parent_id),
            parent_state=nodes[latest_id]["parent_state"],
            confidence=edge["confidence"] if edge else None,
            block_counts=block_counts, opaque_counts=opaque_counts,
            first_conversation=group["evidence"] == "parallel_chains" and edge is None,
        )
        next_cursor = _cursor_encode(order(selected[-1])) if has_more and selected else None
        index = sorted(starts.values(), key=lambda entry: order(entry["newest_request_id"]), reverse=True)
        position_by_id = {rid: position for position, rid in enumerate(ordered)}
        for entry in index:
            position = position_by_id[entry["newest_request_id"]]
            entry["cursor_before"] = _cursor_encode(order(ordered[position - 1])) if position else None
        group_ids = set(ordered)
        roots = [rid for rid in ordered if not (rid in parent_edges and
                 parent_edges[rid]["source_request_id"] in group_ids)]
        views.append({
            "key": group["key"], "label": group["label"], "evidence": group["evidence"],
            "fork_parent_request_id": group["fork_parent_request_id"],
            "latest_request_id": latest_id, "latest_session_seq": nodes[latest_id]["session_seq"],
            "latest_activity": nodes[latest_id]["completed_at"],
            "latest_parent_state": nodes[latest_id]["parent_state"],
            "request_count": len(ordered),
            "unassigned_request_count": len(set(ordered) - confirmed),
            "unlinked_segment_count": max(0, len(roots) - 1),
            "segment_count": len(starts),
            "segment_index": index,
            "recent_segments": segment_slices,
            "has_older_requests": has_more,
            "next_request_cursor": next_cursor,
            "context_change": change,
        })
        if group["key"] == only_group:
            next_id = ordered[ordered.index(selected[-1]) + 1] if selected and has_more else None
            page = {"segments": segment_slices, "next_cursor": next_cursor,
                    "continues_earlier": bool(next_id and segment_by_id[next_id] == segment_by_id[selected[-1]])}
    return views, page


def get_session_conversations(
    db: OrmSession, session_id: str, *, group_offset: int = 0,
    group_limit: int = 4, revision: str | None = None,
    group_key: str | None = None,
) -> dict:
    with db.begin_nested():
        if not get_session(db, session_id):
            raise KeyError("Session not found")
        count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
        graph, current_revision = _session_lineage_graph(db, session_id, count)
        if revision is not None and revision != current_revision:
            raise ValueError("Conversation revision changed; refresh and try again")
        ordered = _conversation_order(graph)
        auxiliary_group = graph.get("auxiliary")
        if group_key is not None:
            selected = next((group for group in ordered if group["key"] == group_key), None)
            if selected is None and (auxiliary_group is None or
                                     auxiliary_group["key"] != group_key):
                raise KeyError("Conversation group not found")
            groups = [selected] if selected is not None else []
            auxiliary_group = auxiliary_group if selected is None else None
        else:
            groups = ordered[group_offset:group_offset + group_limit]
        display_groups = groups + ([auxiliary_group] if auxiliary_group else [])
        views, _ = _conversation_projection_view(db, graph, display_groups) if display_groups else ([], None)
        next_offset = (group_offset + len(groups)
                       if group_key is None and group_offset + len(groups) < len(ordered) else None)
        return {
            "session_id": session_id, "revision": current_revision,
            "conversation_count": graph["conversation_count"],
            "confirmed_parallel_streams": graph["confirmed_parallel_streams"],
            "lineage_fragment_count": graph["lineage_fragment_count"],
            "diagnostic_path_count": graph["diagnostic_path_count"],
            "primary_key": graph["conversations"][0]["key"] if ordered else None,
            "conversations": views[:len(groups)],
            "auxiliary": views[-1] if auxiliary_group else None,
            "auxiliary_request_count": graph["auxiliary_request_count"],
            "next_group_offset": next_offset,
        }


def get_session_conversation_requests(
    db: OrmSession, session_id: str, group_key: str, *, revision: str,
    cursor: str | None = None, limit: int = 50,
) -> dict:
    with db.begin_nested():
        if not get_session(db, session_id):
            raise KeyError("Session not found")
        count = db.scalar(select(func.count()).where(Request.session_id == session_id)) or 0
        graph, current_revision = _session_lineage_graph(db, session_id, count)
        if revision != current_revision:
            raise ValueError("Conversation revision changed; refresh and try again")
        group = next((item for item in graph["conversations"] if item["key"] == group_key), None)
        auxiliary_group = graph.get("auxiliary")
        if group is None and auxiliary_group and auxiliary_group["key"] == group_key:
            group = auxiliary_group
        if group is None:
            raise KeyError("Conversation group not found")
        _, page = _conversation_projection_view(
            db, graph, [group], only_group=group_key, cursor=cursor, request_limit=limit,
        )
        return {"session_id": session_id, "revision": current_revision,
                "group_key": group_key, **page}


def get_dashboard_live(db: OrmSession) -> dict:
    """One SQLite read snapshot for the active session and all displayed groups."""
    # SQLite's legacy SELECT transaction handling does not pin a snapshot for
    # successive reads. A SAVEPOINT makes the first SELECT establish one.
    with db.begin_nested():
        return _get_dashboard_live_snapshot(db)


def _get_dashboard_live_snapshot(db: OrmSession) -> dict:
    session = get_active_session(db)
    if session is None:
        return {
            "active_session": None, "request_flow": [], "activity": [],
            "context_change": None, "conversations": [], "conversation_count": 0,
            "auxiliary": None, "auxiliary_request_count": 0,
            "confirmed_parallel_streams": 0, "lineage_fragment_count": 0,
            "diagnostic_path_count": 0,
            "has_more_conversations": False,
            "most_recent_conversation_key": None,
        }

    totals = db.execute(
        select(
            func.count().label("n"),
            func.coalesce(func.sum(Request.tokens_total_input), 0).label("tok_in"),
            func.coalesce(func.sum(Request.tokens_total_output), 0).label("tok_out"),
        ).where(Request.session_id == session.id)
    ).one()

    active_session = {
        "id": session.id,
        "name": session.name,
        "started_at": session.started_at.isoformat(),
        "request_count": totals.n,
        "tokens_total_input": totals.tok_in,
        "tokens_total_output": totals.tok_out,
    }

    graph, sequence_revision = _session_lineage_graph(db, session.id, totals.n)
    sequence = _session_sequence_projection(db, graph, limit=_CONVERSATION_PREVIEW_LIMIT)
    activity = _session_activity(db, session.id)
    groups = _conversation_order(graph)[:4]
    auxiliary_group = graph.get("auxiliary")
    display_groups = groups + ([auxiliary_group] if auxiliary_group else [])
    views, _ = _conversation_projection_view(
        db, graph, display_groups, preview_limit=_CONVERSATION_PREVIEW_LIMIT,
    ) if display_groups else ([], None)
    conversations = views[:len(groups)]
    auxiliary = views[-1] if auxiliary_group else None

    most_recent = conversations[0] if conversations else auxiliary
    context_change = (_selected_context_change(db, graph, sequence["request_flow"][0]["id"])
                      if sequence["request_flow"] else None)

    return {
        "active_session": active_session,
        "request_flow": sequence["request_flow"],
        "sequence_next_cursor": sequence["next_cursor"],
        "sequence_revision": sequence_revision,
        "activity": activity,
        "context_change": context_change,
        "conversations": conversations,
        "auxiliary": auxiliary,
        "auxiliary_request_count": graph["auxiliary_request_count"],
        "conversation_count": graph["conversation_count"],
        "confirmed_parallel_streams": graph["confirmed_parallel_streams"],
        "lineage_fragment_count": graph["lineage_fragment_count"],
        "diagnostic_path_count": graph["diagnostic_path_count"],
        "has_more_conversations": graph["conversation_count"] > len(conversations),
        "most_recent_conversation_key": most_recent["key"] if most_recent else None,
    }
