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

from contextspy.analysis.context_reconstruction import summarize_context_snapshot
from contextspy.db import crud
from contextspy.db.database import get_db
from contextspy.db.models import Request

router = APIRouter(tags=["requests"])


def _context_payload(db, req: Request) -> dict:
    blocks = crud.get_context_snapshot(db, req.id)
    analysis = summarize_context_snapshot(
        blocks, reconstructed_tokens=req.reconstructed_input_tokens,
    )
    return {
        "request_id": req.id,
        "invocation_seq": req.invocation_seq,
        "lineage": {
            "provider_request_id": req.provider_request_id,
            "previous_provider_request_id": req.previous_provider_request_id,
            "provider_conversation_id": req.provider_conversation_id,
            "logical_turn_id": req.logical_turn_id,
            "lineage_status": req.lineage_status,
        },
        "accounting": {
            "observed_input_tokens": req.observed_input_tokens,
            "reconstructed_input_tokens": req.reconstructed_input_tokens,
            "provider_input_tokens": req.provider_input_tokens,
            "unattributed_input_tokens": req.unattributed_input_tokens,
            "input_token_variance": req.input_token_variance,
            "context_coverage_pct": req.context_coverage_pct,
            "cache_read_tokens": req.cache_read_tokens,
            "cache_write_tokens": req.cache_creation_tokens,
            "uncached_input_tokens": (
                max(req.provider_input_tokens - (req.cache_read_tokens or 0), 0)
                if req.provider_input_tokens is not None else None
            ),
            "ordinary_input_tokens": (
                max(
                    req.provider_input_tokens
                    - (req.cache_read_tokens or 0)
                    - (req.cache_creation_tokens or 0),
                    0,
                ) if req.provider_input_tokens is not None else None
            ),
            "status": req.context_reconstruction_status,
        },
        **analysis,
        "blocks": blocks,
    }


@router.get("/logical-requests")
def list_logical_requests(
    session_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    model: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    status_category: str | None = Query(default=None, pattern="^(success|error)$"),
    sort_by: str = Query(default="timestamp", pattern="^(timestamp|tokens_total_input|tokens_total_output|duration_ms|status_code|session|provider|agent|model)$"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    with get_db() as db:
        rows = crud.list_logical_requests(
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
        return {"logical_requests": [row.to_dict() for row in rows]}


@router.get("/logical-requests/{logical_request_id}")
def get_logical_request(logical_request_id: str):
    with get_db() as db:
        logical = crud.get_logical_request(db, logical_request_id)
        if logical is None:
            raise HTTPException(status_code=404, detail="Logical request not found")
        invocations = crud.get_logical_invocations(db, logical_request_id)
        context = None
        if invocations:
            selected = invocations[-1]
            selection = "final_invocation"
            if selected.lineage_status == "unresolved_predecessor":
                selected = max(
                    invocations,
                    key=lambda row: row.reconstructed_input_tokens or 0,
                )
                selection = "largest_reconstructed_snapshot"
            context = _context_payload(db, selected)
            context["selection"] = selection
        return {
            "logical_request": logical.to_dict(),
            "invocations": [row.to_dict(include_raw=False) for row in invocations],
            "context": context,
        }


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


@router.get("/requests/{request_id}/blocks")
def get_request_blocks(request_id: str):
    with get_db() as db:
        req = crud.get_request(db, request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        blocks = crud.get_blocks(db, request_id)
        return {"session_seq": req.session_seq, "blocks": blocks}


@router.get("/requests/{request_id}/context")
def get_request_context(request_id: str):
    with get_db() as db:
        req = crud.get_request(db, request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return _context_payload(db, req)
