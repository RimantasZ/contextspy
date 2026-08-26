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

import json
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.db.models import (
    BlockContent,
    BlockRecord,
    ContextSnapshotBlock,
    LogicalRequest,
    Request,
    Session,
    ToolStat,
)

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
    db.execute(
        text("UPDATE logical_requests SET session_id = NULL WHERE session_id = :sid"),
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
    db.execute(
        text("DELETE FROM logical_requests WHERE session_id = :sid"),
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
    max_seq = db.execute(
        select(func.max(Request.session_seq)).where(Request.session_id == session_id)
    ).scalar()
    return (max_seq or 0) + 1


def create_request(db: OrmSession, data: dict[str, Any]) -> Request:
    data = dict(data)
    data.setdefault("session_seq", _next_session_seq(db, data.get("session_id")))
    req = Request(**data)
    db.add(req)
    db.flush()
    return req


def get_request(db: OrmSession, request_id: str) -> Request | None:
    return db.get(Request, request_id)


def get_request_by_provider_id(
    db: OrmSession, provider: str, provider_request_id: str,
) -> Request | None:
    return db.execute(
        select(Request).where(
            Request.provider == provider,
            Request.provider_request_id == provider_request_id,
        ).order_by(Request.timestamp.desc())
    ).scalars().first()


def get_latest_conversation_request(
    db: OrmSession, provider: str, conversation_id: str,
) -> Request | None:
    return db.execute(
        select(Request).where(
            Request.provider == provider,
            Request.provider_conversation_id == conversation_id,
        ).order_by(Request.timestamp.desc())
    ).scalars().first()


def get_unresolved_children(
    db: OrmSession, provider: str, provider_request_id: str,
) -> list[Request]:
    return list(db.execute(
        select(Request).where(
            Request.provider == provider,
            Request.previous_provider_request_id == provider_request_id,
            Request.lineage_status == "unresolved_predecessor",
        ).order_by(Request.timestamp.asc())
    ).scalars().all())


def mark_forked_lineage(
    db: OrmSession, provider: str, previous_provider_request_id: str,
) -> bool:
    children = list(db.execute(
        select(Request).where(
            Request.provider == provider,
            Request.previous_provider_request_id == previous_provider_request_id,
        )
    ).scalars().all())
    if len(children) < 2:
        return False
    for child in children:
        child.lineage_status = "forked"
    db.flush()
    return True


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
        stmt = stmt.where(Request.agent == agent)
    if model:
        stmt = stmt.where(Request.model == model)
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
            (Request.status_code >= 200) & (Request.status_code < 300),
            and_(
                Request.transport == "websocket",
                Request.status_code.is_(None),
                Request.response_complete == 1,
                Request.capture_error.is_(None),
                or_(
                    Request.usage_extra.is_(None),
                    ~Request.usage_extra.like('%"ws_error"%'),
                ),
            ),
        ))
    elif status_category == "error":
        stmt = stmt.where(
            or_(
                Request.status_code >= 400,
                Request.capture_error.isnot(None),
                Request.response_complete == 0,
                Request.usage_extra.like('%"ws_error"%'),
            )
        )
    col = Session.name if sort_by == 'session' else _SORT_COLUMNS.get(sort_by, Request.timestamp)
    stmt = stmt.order_by(col.asc() if sort_dir == 'asc' else col.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Logical requests and invocation lineage
# ---------------------------------------------------------------------------

def get_logical_request(db: OrmSession, logical_request_id: str) -> LogicalRequest | None:
    return db.get(LogicalRequest, logical_request_id)


def get_logical_request_by_key(db: OrmSession, grouping_key: str) -> LogicalRequest | None:
    return db.execute(
        select(LogicalRequest).where(LogicalRequest.grouping_key == grouping_key)
    ).scalars().first()


def get_logical_request_by_turn(
    db: OrmSession, *, provider: str, conversation_id: str, turn_id: str,
    session_id: str | None = None,
) -> LogicalRequest | None:
    stmt = select(LogicalRequest).where(
        LogicalRequest.provider == provider,
        LogicalRequest.provider_conversation_id == conversation_id,
        LogicalRequest.logical_turn_id == turn_id,
    )
    stmt = (
        stmt.where(LogicalRequest.session_id.is_(None))
        if session_id is None else stmt.where(LogicalRequest.session_id == session_id)
    )
    return db.execute(stmt.order_by(LogicalRequest.started_at.asc())).scalars().first()


def create_logical_request(db: OrmSession, data: dict[str, Any]) -> LogicalRequest:
    row = LogicalRequest(**data)
    db.add(row)
    db.flush()
    return row


def next_invocation_seq(db: OrmSession, logical_request_id: str) -> int:
    current = db.execute(
        select(func.max(Request.invocation_seq)).where(
            Request.logical_request_id == logical_request_id
        )
    ).scalar()
    return (current or 0) + 1


def attach_request_to_logical(
    db: OrmSession, request: Request, logical_request_id: str,
) -> str | None:
    old_id = request.logical_request_id
    if old_id == logical_request_id:
        return None
    request.logical_request_id = logical_request_id
    request.invocation_seq = next_invocation_seq(db, logical_request_id)
    db.flush()
    if old_id:
        remaining = db.execute(
            select(func.count()).select_from(Request).where(
                Request.logical_request_id == old_id
            )
        ).scalar() or 0
        if remaining == 0:
            old = db.get(LogicalRequest, old_id)
            if old is not None:
                db.delete(old)
                db.flush()
                return None
    return old_id


def get_logical_invocations(
    db: OrmSession, logical_request_id: str,
) -> list[Request]:
    return list(db.execute(
        select(Request).where(Request.logical_request_id == logical_request_id)
        .order_by(Request.invocation_seq.asc(), Request.timestamp.asc())
    ).scalars().all())


_LOGICAL_SORT_COLUMNS = {
    "timestamp": LogicalRequest.started_at,
    "tokens_total_input": LogicalRequest.peak_context_tokens,
    "tokens_total_output": LogicalRequest.cumulative_output_tokens,
    "duration_ms": LogicalRequest.duration_ms,
    "status_code": LogicalRequest.status_code,
    "provider": LogicalRequest.provider,
    "agent": LogicalRequest.agent,
    "model": LogicalRequest.model,
}


def list_logical_requests(
    db: OrmSession,
    session_id: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    q: str | None = None,
    status_category: str | None = None,
    sort_by: str = "timestamp",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[LogicalRequest]:
    stmt = select(LogicalRequest)
    if sort_by == "session":
        stmt = stmt.outerjoin(Session, LogicalRequest.session_id == Session.id)
    if session_id is not None:
        stmt = stmt.where(LogicalRequest.session_id == session_id)
    if provider:
        stmt = stmt.where(LogicalRequest.provider == provider)
    if agent:
        stmt = stmt.where(LogicalRequest.agent == agent)
    if model:
        stmt = stmt.where(LogicalRequest.model == model)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            LogicalRequest.model.ilike(like),
            LogicalRequest.agent.ilike(like),
            LogicalRequest.endpoint.ilike(like),
            LogicalRequest.provider.ilike(like),
        ))
    if status_category == "success":
        stmt = stmt.where(LogicalRequest.state == "complete")
    elif status_category == "error":
        stmt = stmt.where(LogicalRequest.state.in_(("error", "incomplete")))
    column = (
        Session.name if sort_by == "session"
        else _LOGICAL_SORT_COLUMNS.get(sort_by, LogicalRequest.started_at)
    )
    stmt = stmt.order_by(column.asc() if sort_dir == "asc" else column.desc())
    return list(db.execute(stmt.limit(limit).offset(offset)).scalars().all())


def refresh_logical_request(db: OrmSession, logical_request_id: str) -> LogicalRequest:
    logical = db.get(LogicalRequest, logical_request_id)
    if logical is None:
        raise ValueError(f"Unknown logical request: {logical_request_id}")
    rows = get_logical_invocations(db, logical_request_id)
    if not rows:
        return logical

    reported_input = [r.provider_input_tokens for r in rows if r.provider_input_tokens is not None]
    cached = [r.cache_read_tokens for r in rows if r.cache_read_tokens is not None]
    cache_write = [r.cache_creation_tokens for r in rows if r.cache_creation_tokens is not None]
    reported_output = [r.provider_output_tokens for r in rows if r.provider_output_tokens is not None]
    reasoning = [r.provider_reasoning_tokens for r in rows if r.provider_reasoning_tokens is not None]

    def utc_key(row: Request) -> datetime:
        value = row.timestamp
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    logical.invocation_count = len(rows)
    logical.started_at = min(rows, key=utc_key).timestamp
    logical.updated_at = max(rows, key=utc_key).timestamp
    logical.model = rows[-1].model or logical.model
    logical.agent = rows[-1].agent or logical.agent
    logical.peak_context_tokens = max(reported_input) if reported_input else None
    logical.final_context_tokens = next(
        (r.provider_input_tokens for r in reversed(rows) if r.provider_input_tokens is not None),
        None,
    )
    logical.cumulative_input_tokens = sum(reported_input) if reported_input else None
    logical.cumulative_cached_tokens = sum(cached) if cached else None
    logical.cumulative_cache_write_tokens = sum(cache_write) if cache_write else None
    logical.cumulative_output_tokens = sum(reported_output) if reported_output else None
    logical.cumulative_reasoning_tokens = sum(reasoning) if reasoning else None
    durations = [r.duration_ms for r in rows if r.duration_ms is not None]
    logical.duration_ms = sum(durations) if durations else None
    logical.status_code = next(
        (r.status_code for r in reversed(rows) if r.status_code is not None), None,
    )
    def provider_error(row: Request) -> bool:
        if not row.usage_extra:
            return False
        try:
            return bool(json.loads(row.usage_extra).get("ws_error"))
        except (TypeError, json.JSONDecodeError):
            return False

    if any((r.status_code or 0) >= 400 or r.capture_error or provider_error(r) for r in rows):
        logical.state = "error"
    elif any(not bool(r.response_complete) for r in rows):
        logical.state = "incomplete"
    else:
        logical.state = "complete"
    db.flush()
    return logical


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
    logical_request_count = len({r.logical_request_id or r.id for r in rows})

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

    # by_status (exact HTTP status codes)
    by_status: dict[str, int] = {}
    def has_provider_error(row: Request) -> bool:
        if (row.status_code or 0) >= 400 or row.capture_error or not bool(row.response_complete):
            return True
        if not row.usage_extra:
            return False
        try:
            return bool(json.loads(row.usage_extra).get("ws_error"))
        except (TypeError, json.JSONDecodeError):
            return False

    for r in rows:
        if r.status_code is not None:
            key = str(r.status_code)
        elif r.transport == "websocket" and not has_provider_error(r):
            key = "ws_success"
        else:
            key = "unknown"
        by_status[key] = by_status.get(key, 0) + 1

    error_count = sum(1 for row in rows if has_provider_error(row))
    unknown_status_count = by_status.get("unknown", 0)

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
        "model_call_count": len(rows),
        "logical_request_count": logical_request_count,
        "tokens_total_input": total_input,
        "tokens_total_output": total_output,
        "tokens_output_text": output_text,
        "tokens_output_thinking": output_thinking,
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
        "model_call_count": 0,
        "logical_request_count": 0,
        "tokens_total_input": 0,
        "tokens_total_output": 0,
        "tokens_output_text": 0,
        "tokens_output_thinking": 0,
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
# Reconstructed context snapshots
# ---------------------------------------------------------------------------

def insert_context_snapshot(
    db: OrmSession, request_id: str, entries: list[dict[str, Any]],
) -> None:
    db.query(ContextSnapshotBlock).filter(
        ContextSnapshotBlock.request_id == request_id
    ).delete(synchronize_session=False)
    for position, entry in enumerate(entries):
        block = entry["block"]
        if block.content_hash and block.content:
            db.execute(
                sqlite_insert(BlockContent).values(
                    hash=block.content_hash,
                    content=block.content,
                    created_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing(index_elements=["hash"])
            )
        attrs = dict(block.attrs)
        if entry.get("context_operation"):
            attrs["context_operation"] = entry["context_operation"]
        db.add(ContextSnapshotBlock(
            request_id=request_id,
            position=position,
            source_request_id=entry.get("source_request_id"),
            source_block_id=entry.get("source_block_id"),
            direction=block.direction,
            message_index=block.message_index,
            block_type=block.block_type,
            category=block.category,
            content_hash=block.content_hash,
            token_count=block.token_count,
            tool_name=block.tool_name,
            tool_call_id=block.tool_call_id,
            provenance=entry["provenance"],
            attrs=json.dumps(attrs) if attrs else None,
        ))
    db.flush()


def get_context_snapshot(db: OrmSession, request_id: str) -> list[dict]:
    rows = db.execute(
        select(ContextSnapshotBlock, BlockContent.content)
        .outerjoin(BlockContent, ContextSnapshotBlock.content_hash == BlockContent.hash)
        .where(ContextSnapshotBlock.request_id == request_id)
        .order_by(ContextSnapshotBlock.position.asc())
    ).all()
    return [record.to_dict(content) for record, content in rows]


def get_raw_block_rows(
    db: OrmSession, request_id: str, *, direction: str | None = None,
) -> list[tuple[BlockRecord, str | None]]:
    stmt = (
        select(BlockRecord, BlockContent.content)
        .outerjoin(BlockContent, BlockRecord.content_hash == BlockContent.hash)
        .where(BlockRecord.request_id == request_id)
    )
    if direction is not None:
        stmt = stmt.where(BlockRecord.direction == direction)
    stmt = stmt.order_by(BlockRecord.direction.asc(), BlockRecord.position.asc())
    return list(db.execute(stmt).all())


def get_raw_context_rows(
    db: OrmSession, request_id: str,
) -> list[tuple[ContextSnapshotBlock, str | None]]:
    return list(db.execute(
        select(ContextSnapshotBlock, BlockContent.content)
        .outerjoin(BlockContent, ContextSnapshotBlock.content_hash == BlockContent.hash)
        .where(ContextSnapshotBlock.request_id == request_id)
        .order_by(ContextSnapshotBlock.position.asc())
    ).all())


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
    bucket_map = {"minute": "%Y-%m-%dT%H:%M", "hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d"}
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
            func.count(func.distinct(func.coalesce(Request.logical_request_id, Request.id))).label("logical_req_count"),
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
            "logical_req_count": row.logical_req_count,
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
            Request.id,
            Request.logical_request_id,
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
            "request_count": len({r.logical_request_id or r.id for r in reqs}),
            "model_call_count": len(reqs),
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
        "req_count": 0, "logical_req_count": 0, "tok_in": 0, "tok_out": 0,
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
            "request_count": stats["logical_req_count"],
            "model_call_count": stats["req_count"],
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
