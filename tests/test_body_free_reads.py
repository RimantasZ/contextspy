"""Metadata reads must not load the request payload columns.

On an unarchived session every request row carries the whole conversation in its body
columns (megabytes per row). Dashboard reads are refetched on every captured request and
run in the same process as the proxy, so they must only read the columns they need.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from contextspy.db import crud
from contextspy.db.models import Base, Request

_BODY = "x" * 10_000
_BODY_COLUMN_NAMES = (
    "raw_request_body", "canonical_request_body", "raw_response_body",
    "canonical_response_body", "response_events",
)
_T0 = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)


def _request(request_id: str, minutes: int, **overrides) -> Request:
    values = dict(
        id=request_id,
        session_id="s1",
        session_seq=minutes + 1,
        timestamp=_T0 + timedelta(minutes=minutes),
        provider="anthropic",
        agent="claude_code",
        model="claude-sonnet-5-5",
        endpoint="/v1/messages",
        status_code=200,
        invocation_outcome="completed",
        duration_ms=100 * (minutes + 1),
        tokens_total_input=1_000,
        tokens_total_output=50,
        tokens_system_prompt=400,
        tokens_conversation_history=600,
        provider_input_tokens=1_000,
        cache_read_tokens=900,
        cache_creation_tokens=0,
        raw_request_body=_BODY,
        canonical_request_body=_BODY,
        raw_response_body=_BODY,
        canonical_response_body=_BODY,
        response_events="[]",
    )
    values.update(overrides)
    return Request(**values)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            _request("r1", 0),
            _request("r2", 1, provider_response_id="msg_2"),
            _request("r3", 75, status_code=500, invocation_outcome="failed", agent=None),
        ])
        session.commit()
        session.expunge_all()
        yield session


@pytest.fixture
def statements(db):
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        seen.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine, "before_cursor_execute", _record)


def _assert_no_body_reads(statements: list[str]) -> None:
    assert statements, "expected at least one query"
    for statement in statements:
        select_list = statement.split(" FROM ", 1)[0]
        for column in _BODY_COLUMN_NAMES:
            assert column not in select_list, f"{column} loaded by: {statement}"


def test_get_stats_reads_no_bodies_and_keeps_its_result(db, statements):
    stats = crud.get_stats(db)
    _assert_no_body_reads(statements)

    assert stats["request_count"] == 3
    assert stats["tokens_total_input"] == 3_000
    assert stats["by_category"]["system_prompt"] == {"tokens": 1_200, "pct": 40.0}
    assert stats["by_agent"] == {"claude_code": 2, "unknown": 1}
    assert stats["by_status"] == {"200": 2, "500": 1}
    assert stats["error_count"] == 1
    assert stats["latency"]["p50_ms"] == 200
    assert stats["cache"] == {"avg_pct": 90.0, "overall_pct": 90.0, "reporting_request_count": 3}
    assert stats["session_timing"]["elapsed_ms"] == 75 * 60 * 1000
    assert stats["session_timing"]["active_duration_ms"] == 100 + 200 + 7_600


def test_get_stats_for_one_session_reads_no_bodies(db, statements):
    assert crud.get_stats(db, session_id="s1")["request_count"] == 3
    assert crud.get_stats(db, session_id="other")["request_count"] == 0
    _assert_no_body_reads(statements)


def test_get_timeline_reads_no_bodies(db, statements):
    timeline = crud.get_timeline(db, bucket="hour")
    _assert_no_body_reads(statements)
    assert timeline == [
        {"bucket": "2026-11-15T10:00", "request_count": 2, "tokens_total_input": 2_000},
        {"bucket": "2026-11-15T11:00", "request_count": 1, "tokens_total_input": 1_000},
    ]


def test_list_requests_defers_bodies_until_read(db, statements):
    rows = crud.list_requests(db)
    [row.to_dict(include_raw=False) for row in rows]
    _assert_no_body_reads(statements)

    # Callers that do read a body still get it.
    assert rows[0].canonical_request_body == _BODY


def test_context_diff_snapshots_read_no_bodies(db, statements):
    parent, child = crud.get_context_diff_snapshots(db, "r1", "r2")
    _assert_no_body_reads(statements)
    assert (parent.id, child.id) == ("r1", "r2")


def test_request_session_seq_reads_no_bodies(db, statements):
    assert crud.get_request_session_seq(db, "r2") == (True, 2)
    assert crud.get_request_session_seq(db, "missing") == (False, None)
    _assert_no_body_reads(statements)


def test_provider_response_exists_reads_no_bodies(db, statements):
    assert crud.provider_response_exists(db, "anthropic", "msg_2") is True
    assert crud.provider_response_exists(db, "anthropic", "msg_missing") is False
    assert crud.provider_response_exists(db, "openai", "msg_2") is False
    _assert_no_body_reads(statements)
