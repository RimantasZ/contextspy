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
