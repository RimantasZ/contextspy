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

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session as OrmSession

from contextspy.db.models import Request, Session, ToolStat


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


def purge_raw_bodies(db: OrmSession, session_id: str) -> None:
    db.execute(
        text(
            """
            UPDATE requests
            SET raw_request_body = NULL, raw_response_body = NULL
            WHERE session_id = :sid
            """
        ),
        {"sid": session_id},
    )
    db.flush()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def create_request(db: OrmSession, data: dict[str, Any]) -> Request:
    req = Request(**data)
    db.add(req)
    db.flush()
    return req


def get_request(db: OrmSession, request_id: str) -> Request | None:
    return db.get(Request, request_id)


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
        stmt = stmt.where(Request.status_code >= 200, Request.status_code < 300)
    elif status_category == "error":
        stmt = stmt.where(
            or_(Request.status_code == None, Request.status_code >= 400)  # noqa: E711
        )
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
    for r in rows:
        key = str(r.status_code) if r.status_code is not None else "unknown"
        by_status[key] = by_status.get(key, 0) + 1

    return {
        "request_count": len(rows),
        "tokens_total_input": total_input,
        "tokens_total_output": total_output,
        "by_category": by_category,
        "by_provider": by_provider,
        "by_agent": by_agent,
        "by_model": by_model,
        "latency": latency,
        "by_status": by_status,
    }


def _empty_stats() -> dict:
    _empty_latency = {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "min_ms": None, "max_ms": None}
    return {
        "request_count": 0,
        "tokens_total_input": 0,
        "tokens_total_output": 0,
        "by_category": {
            col[len("tokens_"):]: {"tokens": 0, "pct": 0.0}
            for col in _CATEGORY_COLS
        },
        "by_provider": {},
        "by_agent": {},
        "by_model": {},
        "latency": _empty_latency,
        "by_status": {},
    }


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
        entries.append({
            "type": "gap",
            "session_id": None,
            "name": None,
            "started_at": reqs[0].timestamp.isoformat(),
            "ended_at": reqs[-1].timestamp.isoformat(),
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
        entries.append({
            "type": "session",
            "session_id": s.id,
            "name": s.name,
            "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "is_active": bool(s.is_active),
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
