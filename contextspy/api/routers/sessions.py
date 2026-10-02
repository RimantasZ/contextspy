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

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from contextspy.api.websocket import ConnectionManager
from contextspy.db import crud
from contextspy.db.database import get_db

router = APIRouter(tags=["sessions"])


class CreateSessionRequest(BaseModel):
    name: str


class RenameSessionRequest(BaseModel):
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


@router.get("/sessions/{session_id}/lineage")
def get_session_lineage(session_id: str):
    """Return the complete, backend-derived invocation graph for one session."""
    with get_db() as db:
        session = crud.get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        with db.begin_nested():
            graph = crud.get_session_lineage_graph(db, session_id)
        return {
            "capture": session.to_dict(),
            "session": session.to_dict(),
            **graph,
            "nodes": crud.annotated_lineage_nodes(graph),
        }


@router.get("/sessions/{session_id}/lineage/revision")
def get_session_lineage_revision(session_id: str):
    """Small polling response so diagnostics reloads only when evidence changes."""
    with get_db() as db:
        if not crud.get_session(db, session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        with db.begin_nested():
            return {"revision": crud.get_session_lineage_revision(db, session_id)}


@router.get("/sessions/{session_id}/conversations")
def get_session_conversations(
    session_id: str, group_offset: int = Query(0, ge=0),
    group_limit: int = Query(4, ge=1, le=20), revision: str | None = None,
    group_key: str | None = None,
):
    try:
        with get_db() as db:
            return crud.get_session_conversations(
                db, session_id, group_offset=group_offset,
                group_limit=group_limit, revision=revision, group_key=group_key,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/conversations/requests")
def get_session_conversation_requests(
    session_id: str, group_key: str, revision: str,
    cursor: str | None = None, limit: int = Query(50, ge=1, le=100),
):
    try:
        with get_db() as db:
            return crud.get_session_conversation_requests(
                db, session_id, group_key, revision=revision, cursor=cursor, limit=limit,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/sequence")
def get_session_sequence(
    session_id: str, revision: str | None = None,
    cursor: str | None = None, limit: int = Query(15, ge=1, le=100),
):
    try:
        with get_db() as db:
            return crud.get_session_sequence(db, session_id, revision=revision,
                                             cursor=cursor, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/sequence/{request_id}/context")
def get_session_request_context(session_id: str, request_id: str,
                                revision: str | None = None):
    try:
        with get_db() as db:
            return crud.get_session_request_context(db, session_id, request_id,
                                                    revision=revision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
def delete_session(session_id: str, delete_requests: bool = False):
    with get_db() as db:
        if delete_requests:
            ok = crud.delete_session_with_requests(db, session_id)
        else:
            ok = crud.delete_session(db, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: RenameSessionRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    with get_db() as db:
        session = crud.rename_session(db, session_id, name)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session": session.to_dict()}
