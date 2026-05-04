from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from contextspy.api.websocket import ConnectionManager
from contextspy.db import crud
from contextspy.db.database import get_db

router = APIRouter(tags=["sessions"])


class CreateSessionRequest(BaseModel):
    name: str


def _get_ws() -> ConnectionManager:
    from contextspy.api.main import get_ws_manager
    return get_ws_manager()


@router.post("/sessions")
def create_session(body: CreateSessionRequest):
    warning = None
    with get_db() as db:
        active = crud.get_active_session(db)
        if active:
            crud.end_session(db, active.id)
            warning = f"Previous session '{active.name}' was automatically ended."
        session = crud.create_session(db, body.name)
        result = session.to_dict()

    ws = _get_ws()
    if ws.loop:
        asyncio.run_coroutine_threadsafe(
            ws.broadcast({"event": "session_started", "data": result}),
            ws.loop,
        )
    return {"session": result, "warning": warning}


@router.get("/sessions")
def list_sessions():
    with get_db() as db:
        sessions = crud.list_sessions(db)
        return {"sessions": [s.to_dict() for s in sessions]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    with get_db() as db:
        session = crud.get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        stats = crud.get_stats(db, session_id)
        return {"session": session.to_dict(), "stats": stats}


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str):
    with get_db() as db:
        session = crud.end_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        result = session.to_dict()

    ws = _get_ws()
    if ws.loop:
        asyncio.run_coroutine_threadsafe(
            ws.broadcast({"event": "session_ended", "data": result}),
            ws.loop,
        )
    return {"session": result}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    with get_db() as db:
        ok = crud.delete_session(db, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}
