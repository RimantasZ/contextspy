"""SQLite concurrency and capture retry regression tests."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from contextspy.analysis.blocks import Block, BlockType, Direction
from contextspy.db import crud, database
from contextspy.proxy import capture_writer
from contextspy.proxy.addon import ContextSpyAddon
from contextspy.proxy.capture_writer import CaptureEnvelope


def _locked_error() -> OperationalError:
    return OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))


def _envelope(request_id: str, session_id: str) -> CaptureEnvelope:
    return CaptureEnvelope(
        request_id=request_id,
        provider="anthropic",
        provider_response_id=f"response-{request_id}",
        transport="http",
        data={
            "id": request_id,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc),
            "provider": "anthropic",
            "endpoint": "/v1/messages",
            "provider_response_id": f"response-{request_id}",
        },
        blocks=(Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "hello"),),
        tool_rows=({"tool_name": "test_tool", "definition_tokens": 5},),
    )


def test_delete_reader_blocks_commit_but_wal_preserves_snapshot(tmp_path):
    delete_path = tmp_path / "delete.db"
    with sqlite3.connect(delete_path) as setup:
        setup.execute("CREATE TABLE items (value INTEGER)")
        setup.execute("INSERT INTO items VALUES (1)")
    reader = sqlite3.connect(delete_path, timeout=0.05)
    writer = sqlite3.connect(delete_path, timeout=0.05)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        writer.execute("INSERT INTO items VALUES (2)")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            writer.commit()
    finally:
        writer.rollback()
        reader.rollback()
        writer.close()
        reader.close()

    wal_path = tmp_path / "wal.db"
    database.init_db(wal_path)
    with sqlite3.connect(wal_path) as setup:
        setup.execute("CREATE TABLE items (value INTEGER)")
        setup.execute("INSERT INTO items VALUES (1)")
    reader = sqlite3.connect(wal_path, timeout=0.05)
    writer = sqlite3.connect(wal_path, timeout=0.05)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        writer.execute("INSERT INTO items VALUES (2)")
        writer.commit()
        assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        reader.rollback()
        assert reader.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    finally:
        writer.close()
        reader.close()
        database.dispose_engine()


def test_init_db_enables_wal_and_timeout_on_reopen(tmp_path):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE legacy_marker (value TEXT)")
        old.execute("INSERT INTO legacy_marker VALUES ('preserved')")
        assert old.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    database.init_db(path)
    with database.get_engine().connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 250
        assert conn.exec_driver_sql("SELECT value FROM legacy_marker").scalar_one() == "preserved"
    with database.get_db() as db:
        crud.create_session(db, "existing")
    database.init_db(path)
    with database.get_db() as db:
        assert crud.get_active_session(db).name == "existing"
    database.dispose_engine()


def test_init_db_fails_when_file_cannot_enter_wal(tmp_path):
    # A directory cannot be opened as a SQLite database. Startup must fail
    # rather than silently proceed without WAL.
    with pytest.raises(RuntimeError, match="Could not enable SQLite WAL"):
        database.init_db(tmp_path)


def test_pinned_dashboard_reader_does_not_block_capture(tmp_path):
    database.init_db(tmp_path / "overlap.db")
    with database.get_db() as db:
        session = crud.create_session(db, "capture")
        session_id = session.id
    capture_writer.persist_capture(_envelope("initial", session_id))
    reader_ready = threading.Event()
    release_reader = threading.Event()
    reader_errors = []

    def hold_snapshot():
        try:
            with database.get_db() as db:
                db.execute(text("BEGIN"))
                crud.get_dashboard_live(db)
                first_count = db.execute(text("SELECT COUNT(*) FROM requests")).scalar_one()
                reader_ready.set()
                assert release_reader.wait(5)
                assert db.execute(text("SELECT COUNT(*) FROM requests")).scalar_one() == first_count
        except Exception as exc:
            reader_errors.append(exc)
            reader_ready.set()

    reader = threading.Thread(target=hold_snapshot)
    reader.start()
    try:
        assert reader_ready.wait(5)
        assert not reader_errors
        started = time.monotonic()
        for index in range(10):
            capture_writer.persist_capture(_envelope(f"while-reading-{index}", session_id))
        assert time.monotonic() - started < 1.5
    finally:
        release_reader.set()
        reader.join(timeout=5)
    assert not reader_errors
    with database.get_db() as db:
        rows = crud.list_requests(db, session_id=session_id, limit=20)
        assert len(rows) == 11
        assert sorted(row.session_seq for row in rows) == list(range(1, 12))
    database.dispose_engine()


def test_capture_retries_commit_with_one_atomic_row(tmp_path, monkeypatch):
    database.init_db(tmp_path / "retry.db")
    with database.get_db() as db:
        session = crud.create_session(db, "capture")
        session_id = session.id
    real_get_db = database.get_db
    sessions = []

    @contextmanager
    def fail_first_commit():
        with real_get_db() as db:
            sessions.append(db)
            if len(sessions) == 1:
                original_commit = db.commit

                def locked_commit():
                    db.commit = original_commit
                    raise _locked_error()

                db.commit = locked_commit
            yield db

    monkeypatch.setattr(capture_writer, "get_db", fail_first_commit)
    payload = capture_writer.persist_capture(_envelope("retry-commit", session_id))
    assert payload["id"] == "retry-commit"
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    with real_get_db() as db:
        request = crud.get_request(db, "retry-commit")
        assert request.session_seq == 1
        assert len(crud.get_blocks(db, request.id)) == 1
        assert len(crud.get_tool_stats(db, request_id=request.id)) == 1
    database.dispose_engine()


def test_capture_retries_after_write_rollback(tmp_path, monkeypatch):
    database.init_db(tmp_path / "retry-write.db")
    with database.get_db() as db:
        session = crud.create_session(db, "capture")
        session_id = session.id
    original_insert = crud.insert_blocks
    attempts = 0

    def locked_once(db, request_id, blocks):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _locked_error()
        return original_insert(db, request_id, blocks)

    monkeypatch.setattr(crud, "insert_blocks", locked_once)
    capture_writer.persist_capture(_envelope("retry-write", session_id))
    assert attempts == 2
    with database.get_db() as db:
        request = crud.get_request(db, "retry-write")
        assert request.session_seq == 1
        assert len(crud.get_blocks(db, request.id)) == 1
    database.dispose_engine()


def test_capture_recovers_from_real_sqlite_writer_lock(tmp_path, monkeypatch):
    path = tmp_path / "real-lock.db"
    database.init_db(path)
    with database.get_db() as db:
        session = crud.create_session(db, "capture")
        session_id = session.id
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    locked_seen = threading.Event()
    original_check = capture_writer._is_transient_sqlite_lock

    def observe_lock(exc):
        transient = original_check(exc)
        if transient:
            locked_seen.set()
        return transient

    monkeypatch.setattr(capture_writer, "_is_transient_sqlite_lock", observe_lock)
    outcome = []

    def capture():
        try:
            outcome.append(capture_writer.persist_capture(_envelope("real-lock", session_id)))
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=capture)
    worker.start()
    try:
        assert locked_seen.wait(3)
    finally:
        blocker.rollback()
        blocker.close()
        worker.join(timeout=5)
    assert len(outcome) == 1 and isinstance(outcome[0], dict)
    assert outcome[0]["id"] == "real-lock"
    with database.get_db() as db:
        assert crud.get_request(db, "real-lock").session_seq == 1
    database.dispose_engine()


def test_non_lock_operational_error_is_not_retried(monkeypatch):
    attempts = 0

    def fail(_envelope):
        nonlocal attempts
        attempts += 1
        raise OperationalError("SELECT", {}, sqlite3.OperationalError("no such table: x"))

    monkeypatch.setattr(capture_writer, "_persist_once", fail)
    with pytest.raises(OperationalError, match="no such table"):
        capture_writer.persist_capture(_envelope("non-lock", "session"))
    assert attempts == 1


def test_exhausted_lock_logs_capture_not_saved(monkeypatch, caplog):
    monkeypatch.setattr(capture_writer, "_RETRY_BUDGET_SECONDS", 0)
    monkeypatch.setattr(capture_writer, "_persist_once", lambda _: (_ for _ in ()).throw(_locked_error()))
    with pytest.raises(OperationalError, match="database is locked"):
        capture_writer.persist_capture(_envelope("exhausted", "session"))
    assert "capture_not_saved request_id=exhausted" in caplog.text


def test_addon_broadcasts_once_after_retry(tmp_path, monkeypatch):
    database.init_db(tmp_path / "broadcast.db")
    with database.get_db() as db:
        session = crud.create_session(db, "capture")
        session_id = session.id
    original_persist_once = capture_writer._persist_once
    attempts = 0

    def locked_once(envelope):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _locked_error()
        return original_persist_once(envelope)

    monkeypatch.setattr(capture_writer, "_persist_once", locked_once)
    events = []

    class Manager:
        loop = object()

        async def broadcast(self, event):
            events.append(event)

    def schedule(coroutine, _loop):
        asyncio.run(coroutine)

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", schedule)
    addon = ContextSpyAddon()
    addon.ws_manager = Manager()
    addon._save_request(
        provider="anthropic", agent="claude_code", endpoint="/v1/messages",
        req_body={"model": "test"}, analyzed=None, duration_ms=1,
        raw_resp_text="{}", status_code=200, raw_request_body="{}",
        session_id=session_id,
    )
    assert attempts == 2
    assert len(events) == 1 and events[0]["event"] == "new_request"
    with database.get_db() as db:
        assert len(crud.list_requests(db, session_id=session_id)) == 1
    database.dispose_engine()
