from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from contextspy.db import crud
from contextspy.db.models import Base, Request


def _request(request_id: str, agent: str | None) -> Request:
    return Request(
        id=request_id,
        timestamp=datetime.now(timezone.utc),
        provider="openai_chatgpt",
        agent=agent,
        endpoint="/backend-api/codex/responses",
    )


def test_unknown_agent_filter_matches_requests_without_agent_metadata():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add_all([
            _request("missing-agent", None),
            _request("literal-unknown", "unknown"),
            _request("codex", "codex"),
        ])
        db.commit()

        result = crud.list_requests(db, agent="unknown")

    assert {request.id for request in result} == {"missing-agent", "literal-unknown"}


def test_purpose_filter_matches_exact_values_and_treats_unclassified_as_unknown():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    def request(request_id: str, purpose: str | None) -> Request:
        row = _request(request_id, "codex")
        row.purpose = purpose
        return row

    with Session(engine) as db:
        db.add_all([
            request("turn", "user_turn"),
            request("continuation", "tool_continuation"),
            request("explicit-unknown", "unknown"),
            request("never-classified", None),
        ])
        db.commit()

        assert {r.id for r in crud.list_requests(db, purpose="user_turn")} == {"turn"}
        assert {r.id for r in crud.list_requests(db, purpose="tool_continuation")} == {"continuation"}
        assert {r.id for r in crud.list_requests(db, purpose="unknown")} == {"explicit-unknown", "never-classified"}
        assert {r.id for r in crud.list_requests(db, purpose="compaction")} == set()  # reserved value
        assert len(crud.list_requests(db)) == 4


def test_purpose_filter_is_exposed_by_the_requests_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from contextspy.db.database import get_db, init_db

    init_db(tmp_path / "filters.db")
    with get_db() as db:
        for request_id, purpose in (("a", "user_turn"), ("b", "tool_continuation")):
            crud.create_request(db, {
                "id": request_id, "timestamp": datetime.now(timezone.utc), "provider": "x",
                "endpoint": "/v1/messages", "purpose": purpose,
            })
    # The route reads the module-level database initialised above, so a bare router app is enough.
    from fastapi import FastAPI
    from contextspy.api.routers import requests as requests_router

    app = FastAPI()
    app.include_router(requests_router.router, prefix="/api")
    client = TestClient(app)
    body = client.get("/api/requests", params={"purpose": "tool_continuation"}).json()
    assert [r["id"] for r in body["requests"]] == ["b"]
    assert body["requests"][0]["purpose"] == "tool_continuation"
    assert client.get("/api/requests", params={"purpose": "bogus"}).status_code == 422
