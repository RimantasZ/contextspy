from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from token_scrooge.api.websocket import ConnectionManager
from token_scrooge.db import crud
from token_scrooge.db.database import get_db

router = APIRouter(tags=["sessions"])


class CreateSessionRequest(BaseModel):
    name: str


def _get_ws() -> ConnectionManager:
    from token_scrooge.api.main import get_ws_manager
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
def end_session(session_id: str, background_tasks: BackgroundTasks):
    with get_db() as db:
        session = crud.end_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        result = session.to_dict()

    background_tasks.add_task(_purge_raw_bodies, session_id)

    ws = _get_ws()
    if ws.loop:
        asyncio.run_coroutine_threadsafe(
            ws.broadcast({"event": "session_ended", "data": result}),
            ws.loop,
        )
    return {"session": result}


def _purge_raw_bodies(session_id: str) -> None:
    with get_db() as db:
        crud.purge_raw_bodies(db, session_id)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    with get_db() as db:
        ok = crud.delete_session(db, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}
