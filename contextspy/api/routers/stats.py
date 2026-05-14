from __future__ import annotations

from fastapi import APIRouter, Query

from contextspy.db import crud
from contextspy.db.database import get_db

router = APIRouter(tags=["stats"])


@router.get("/stats/overview")
def stats_overview():
    with get_db() as db:
        return crud.get_stats(db)


@router.get("/stats/session/{session_id}")
def stats_session(session_id: str):
    with get_db() as db:
        return crud.get_stats(db, session_id=session_id)


@router.get("/stats/timeline")
def stats_timeline(
    session_id: str | None = Query(default=None),
    bucket: str = Query(default="hour", pattern="^(minute|hour|day)$"),
):
    with get_db() as db:
        return {"timeline": crud.get_timeline(db, session_id=session_id, bucket=bucket)}


@router.get("/stats/tools")
def stats_tools(
    session_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
):
    with get_db() as db:
        return {"tools": crud.get_tool_stats(db, session_id=session_id, request_id=request_id)}


@router.get("/stats/sessions-summary")
def stats_sessions_summary():
    with get_db() as db:
        return {"entries": crud.get_sessions_summary(db)}
