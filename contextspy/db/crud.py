from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
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


def list_requests(
    db: OrmSession,
    session_id: str | None = None,
    provider: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Request]:
    q = select(Request).order_by(Request.timestamp.desc())
    if session_id is not None:
        q = q.where(Request.session_id == session_id)
    if provider:
        q = q.where(Request.provider == provider)
    if agent:
        q = q.where(Request.agent == agent)
    q = q.limit(limit).offset(offset)
    return list(db.execute(q).scalars().all())


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

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

    return {
        "request_count": len(rows),
        "tokens_total_input": total_input,
        "tokens_total_output": total_output,
        "by_category": by_category,
        "by_provider": by_provider,
        "by_agent": by_agent,
    }


def _empty_stats() -> dict:
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
