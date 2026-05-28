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

from fastapi import APIRouter, HTTPException, Query

from contextspy.db import crud
from contextspy.db.database import get_db

router = APIRouter(tags=["requests"])


@router.get("/requests")
def list_requests(
    session_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    model: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    status_category: str | None = Query(default=None, pattern="^(success|error)$"),
    sort_by: str = Query(default='timestamp', pattern="^(timestamp|tokens_total_input|tokens_total_output|duration_ms|status_code|session|provider|agent|model)$"),
    sort_dir: str = Query(default='desc', pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with get_db() as db:
        reqs = crud.list_requests(
            db,
            session_id=session_id,
            provider=provider,
            agent=agent,
            model=model,
            q=q,
            status_category=status_category,
            sort_by=sort_by,
            sort_dir=sort_dir,
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
