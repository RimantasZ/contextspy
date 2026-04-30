from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from token_scrooge.db import crud
from token_scrooge.db.database import get_db

router = APIRouter(tags=["requests"])


@router.get("/requests")
def list_requests(
    session_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with get_db() as db:
        reqs = crud.list_requests(
            db,
            session_id=session_id,
            provider=provider,
            agent=agent,
            limit=limit,
            offset=offset,
        )
        return {"requests": [r.to_dict(include_raw=False) for r in reqs]}


@router.get("/requests/{request_id}")
def get_request(request_id: str):
    with get_db() as db:
        req = crud.get_request(db, request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return {"request": req.to_dict(include_raw=True)}
