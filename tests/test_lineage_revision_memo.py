"""The remembered lineage revision: exact for every in-process write, cheap otherwise."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.blocks import Block, BlockType, Direction
from contextspy.db import crud, database, session_archive, session_lineage_service as service
from contextspy.db.models import Base, BlockRecord, Request, Session
from contextspy.proxy import capture_writer
from contextspy.proxy.capture_writer import CaptureEnvelope

T0 = datetime(2026, 9, 18, 8, 0, 0)


@pytest.fixture(autouse=True)
def _clean_memo():
    service.invalidate()
    yield
    service.invalidate()


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memo.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    service.watch_engine(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def counted(monkeypatch):
    """Counts how often the exact fingerprint is computed."""
    calls = []
    real = service.evidence_revision

    def counting(db, session_id, session_count):
        calls.append(session_id)
        return real(db, session_id, session_count)

    monkeypatch.setattr(service, "evidence_revision", counting)
    return calls


def _seed(engine, sid="s1", requests=2):
    with OrmSession(engine) as db:
        db.add(Session(id=sid, name=sid, started_at=T0, is_active=1))
        for seq in range(1, requests + 1):
            db.add(Request(
                id=f"{sid}-r{seq}", session_id=sid, session_seq=seq,
                timestamp=T0 + timedelta(minutes=seq), provider="anthropic",
                endpoint="/v1/messages", provider_response_id=f"{sid}-resp{seq}",
                tokens_total_input=10, tokens_total_output=1, context_fidelity="complete",
                raw_request_body="body", raw_response_body="body"))
            db.add(BlockRecord(request_id=f"{sid}-r{seq}", direction="input", position=0,
                               block_type="user_message", content_hash=f"h{seq}", token_count=3))
        db.commit()


def _revision(engine, sid="s1"):
    with OrmSession(engine) as db:
        return crud.get_session_lineage_revision(db, sid)


def _exact(engine, sid="s1"):
    with OrmSession(engine) as db:
        count = db.scalar(text("select count(*) from requests where session_id = :s"), {"s": sid})
        return service.evidence_revision(db, sid, count)


def _write(engine, sql, **params):
    with OrmSession(engine) as db:
        db.execute(text(sql), params)
        db.commit()


def test_repeated_calls_compute_the_fingerprint_once(engine, counted):
    _seed(engine)
    first = _revision(engine)
    assert [_revision(engine) for _ in range(5)] == [first] * 5
    assert len(counted) == 1


def test_reading_does_not_invalidate(engine, counted):
    _seed(engine)
    _revision(engine)
    with OrmSession(engine) as db:
        db.execute(text("select * from requests")).all()
        db.commit()
    with OrmSession(engine) as db:
        db.execute(text("select * from blocks")).all()
        db.rollback()
    _revision(engine)
    assert len(counted) == 1


@pytest.mark.parametrize("sql", [
    "update requests set tokens_total_input = 999 where id = 's1-r1'",
    "update blocks set content_hash = 'edited' where request_id = 's1-r1'",
    "update requests set session_id = null where id = 's1-r2'",
    "delete from blocks where request_id = 's1-r2'",
    "delete from requests where id = 's1-r2'",
])
def test_a_committed_write_changes_the_revision_on_the_next_call(engine, sql):
    _seed(engine)
    before = _revision(engine)
    _write(engine, sql)
    after = _revision(engine)
    assert after != before
    assert after == _exact(engine)


def test_a_write_is_seen_by_the_transaction_that_made_it(engine):
    _seed(engine)
    with OrmSession(engine) as db:
        before = crud.get_session_lineage_revision(db, "s1")
        db.execute(text("update blocks set token_count = 77 where request_id = 's1-r1'"))
        assert crud.get_session_lineage_revision(db, "s1") != before  # not yet committed
        db.rollback()
    assert _revision(engine) == before == _exact(engine)


def test_a_rolled_back_write_leaves_the_revision_of_the_stored_data(engine):
    _seed(engine)
    before = _revision(engine)
    with OrmSession(engine) as writer:
        writer.execute(text("update blocks set token_count = 77 where request_id = 's1-r1'"))
        # A reader on another connection memoises while the write is pending.
        assert _revision(engine) == before
        writer.rollback()
    assert _revision(engine) == before == _exact(engine)


def test_a_commit_after_a_reader_computed_is_seen(engine):
    _seed(engine)
    with OrmSession(engine) as writer:
        writer.execute(text("update blocks set token_count = 77 where request_id = 's1-r1'"))
        pending = _revision(engine)  # computed from the data before the commit
        writer.commit()
    assert _revision(engine) != pending
    assert _revision(engine) == _exact(engine)


def test_a_session_delete_changes_the_revision(engine):
    _seed(engine)
    before = _revision(engine)
    with OrmSession(engine) as db:
        crud.delete_session(db, "s1")
        db.commit()
    assert _revision(engine) != before
    _seed(engine, "s2")
    before = _revision(engine, "s2")
    with OrmSession(engine) as db:
        crud.delete_session_with_requests(db, "s2")
        db.commit()
    assert _revision(engine, "s2") != before


def test_a_request_of_another_session_that_answers_a_predecessor_changes_the_revision(engine):
    _seed(engine, "s1", requests=1)
    _write(engine, "update requests set predecessor_response_id = 'elsewhere' where id = 's1-r1'")
    before = _revision(engine)
    _seed(engine, "s2", requests=1)
    assert _revision(engine) == before  # unrelated
    _write(engine, "update requests set provider_response_id = 'elsewhere' where id = 's2-r1'")
    after = _revision(engine)
    assert after != before
    assert after == _exact(engine)


def test_archiving_does_not_change_the_revision(engine):
    _seed(engine)
    with OrmSession(engine) as db:
        db.get(Session, "s1").is_active = 0
        db.commit()
    before = _revision(engine)
    with OrmSession(engine) as db:
        session_archive.archive_session_data(db, "s1")
    assert _exact(engine) == before
    assert _revision(engine) == before


def test_a_write_during_the_computation_is_not_remembered(engine, monkeypatch):
    _seed(engine)
    real = service.evidence_revision
    state = {"first": True}

    def racing(db, session_id, session_count):
        revision = real(db, session_id, session_count)
        if state.pop("first", False):
            _write(engine, "update blocks set token_count = 77 where request_id = 's1-r1'")
        return revision

    monkeypatch.setattr(service, "evidence_revision", racing)
    stale = _revision(engine)
    assert _revision(engine) != stale  # the stale value was served once, never kept
    assert _revision(engine) == _exact(engine)


def test_a_write_from_outside_the_process_is_seen_through_the_count_or_the_ttl(engine, monkeypatch):
    _seed(engine)
    clock = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    before = _revision(engine)

    outside = create_engine(engine.url)  # never read through, so no events: another process

    def raw(sql):
        _write(outside, sql)

    raw("update blocks set token_count = 77 where request_id = 's1-r1'")
    assert _revision(engine) == before  # an in-place edit from another process: until the TTL
    clock[0] += service.REVISION_TTL_SECONDS + 1
    after = _revision(engine)
    assert after != before and after == _exact(engine)

    with OrmSession(outside) as db:
        db.add(Request(id="late", session_id="s1", session_seq=9, timestamp=T0, provider="anthropic",
                       endpoint="/v1/messages"))
        db.commit()
    assert _revision(engine) != after  # an append changes the count: seen at once
    outside.dispose()


def test_the_memo_is_bounded(engine, monkeypatch):
    monkeypatch.setattr(service, "REVISION_MEMO_LIMIT", 3)
    for index in range(6):
        _seed(engine, f"s{index}", requests=1)
        _revision(engine, f"s{index}")
    assert len(service._revisions) <= 3


@pytest.mark.parametrize("statement,expected", [
    ("INSERT INTO requests (id) VALUES (?)", True),
    ("INSERT OR IGNORE INTO blocks (id) VALUES (?)", True),
    ("UPDATE requests SET session_id=? WHERE id=?", True),
    ('UPDATE "blocks" SET position=?', True),
    ("DELETE FROM requests WHERE session_id = ?", True),
    ("  delete from blocks where request_id = ?", True),
    ("SELECT * FROM requests", False),
    ("INSERT INTO tool_stats (request_id) VALUES (?)", False),
    ("INSERT INTO block_contents (hash) VALUES (?)", False),
    ("UPDATE sessions SET name=? WHERE id=?", False),
    ("DELETE FROM block_contents WHERE hash IN (SELECT b.content_hash FROM blocks b)", False),
])
def test_only_writes_to_the_evidence_tables_count(statement, expected):
    assert service._writes_evidence(statement) is expected


def _envelope(request_id: str, session_id: str) -> CaptureEnvelope:
    return CaptureEnvelope(
        request_id=request_id, provider="anthropic", provider_response_id=f"response-{request_id}",
        transport="http",
        data={"id": request_id, "session_id": session_id, "timestamp": datetime.now(timezone.utc),
              "provider": "anthropic", "endpoint": "/v1/messages",
              "provider_response_id": f"response-{request_id}"},
        blocks=(Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "hello"),),
        tool_rows=(),
    )


def test_a_captured_request_changes_the_revision_without_waiting(tmp_path):
    database.init_db(tmp_path / "capture.db")
    try:
        with database.get_db() as db:
            session_id = crud.create_session(db, "capture").id
        capture_writer.persist_capture(_envelope("first", session_id))
        with database.get_db() as db:
            first = crud.get_session_lineage_revision(db, session_id)
            assert crud.get_session_lineage_revision(db, session_id) == first
        capture_writer.persist_capture(_envelope("second", session_id))
        with database.get_db() as db:
            second = crud.get_session_lineage_revision(db, session_id)
            count = db.scalar(text("select count(*) from requests"))
            assert second != first
            assert second == service.evidence_revision(db, session_id, count)
    finally:
        database.dispose_engine()
