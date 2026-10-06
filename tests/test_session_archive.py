"""Session archive: payloads and unreferenced block text go, analysis data stays."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contextspy import cli
from contextspy.analysis.blocks import Block, BlockType, Direction
from contextspy.api.routers import requests as requests_router, sessions as sessions_router
from contextspy.config import RetentionSettings, Settings
from contextspy.db import block_occurrence_service, crud, database, session_archive
from contextspy.db.database import get_db, init_db
from contextspy.db.models import BlockContent, BlockRecord, Request

T0 = datetime(2026, 10, 6, 8, 0, 0, tzinfo=timezone.utc)
BODY = "x" * 4000


def prose(size: int, seed: int = 0) -> str:
    """Natural-looking text: the tokenizer is pathologically slow on a long run of one repeated character."""
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    text = " ".join(words[(i * 7 + seed) % len(words)] + str(i % 13) for i in range(size // 7))
    return text[:size]
BODY_COLUMNS = ("raw_request_body", "raw_response_body", "canonical_request_body", "canonical_response_body", "response_events")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "archive.db"
    init_db(path)
    yield path
    database.dispose_engine()


def _session(name, *, ended=True):
    with get_db() as db:
        session = crud.create_session(db, name)
        session_id = session.id
        if ended:
            crud.end_session(db, session_id)
    return session_id


def _request(request_id, session_id, *, texts=(), bodies=True, minutes=0, **extra):
    """A request with one user-message block per text (each text becomes a block_contents row)."""
    with get_db() as db:
        data = {
            "id": request_id, "session_id": session_id, "timestamp": T0 + timedelta(minutes=minutes),
            "provider": "anthropic", "endpoint": "/v1/messages", "tokens_total_input": 10, "tokens_total_output": 2,
            "provider_response_id": request_id,
        }
        if bodies:
            data.update({column: BODY for column in BODY_COLUMNS})
            data["response_events"] = "[]"          # the API decodes this column as JSON
        data.update(extra)                           # explicit values win over the placeholder bodies
        crud.create_request(db, data)
        crud.insert_blocks(db, request_id, [
            Block.make(Direction.INPUT, BlockType.USER_MESSAGE, text, message_index=index, json_path=("messages", index, "content"))
            for index, text in enumerate(texts)
        ])


def _row(request_id):
    with get_db() as db:
        r = db.get(Request, request_id)
        return {column: getattr(r, column) for column in BODY_COLUMNS}


def _contents():
    with get_db() as db:
        return {row[0] for row in db.execute(BlockContent.__table__.select().with_only_columns(BlockContent.content))}


def _archive(session_id):
    with get_db() as db:
        outcome = session_archive.archive_session_data(db, session_id)
        return {**outcome, "session": outcome["session"].to_dict()}


# --------------------------------------------------------------------------- core behaviour

def test_archive_removes_payloads_marks_the_session_and_keeps_every_block_row(db_path):
    sid = _session("old")
    _request("a", sid, texts=("alpha", "beta"))
    _request("b", sid, texts=("beta", "gamma"), minutes=1)
    with get_db() as db:
        before = [(b.id, b.request_id, b.content_hash, b.token_count, b.json_path) for b in db.query(BlockRecord).order_by(BlockRecord.id)]

    outcome = _archive(sid)

    assert outcome["session"]["status"] == "archived" and outcome["session"]["archived_at"]
    assert outcome["already_archived"] is False
    assert all(value is None for request in ("a", "b") for value in _row(request).values())
    with get_db() as db:
        after = [(b.id, b.request_id, b.content_hash, b.token_count, b.json_path) for b in db.query(BlockRecord).order_by(BlockRecord.id)]
        blocks = crud.get_blocks(db, "a")
    assert after == before                                   # same rows, hashes, tokens, JSON paths
    assert all(b["content"] is None and b["content_purged"] for b in blocks)
    assert _contents() == set()


def test_freed_counts_equal_what_was_removed(db_path):
    sid = _session("old")
    _request("a", sid, texts=("alpha", "beta"))
    _request("b", sid, texts=("beta",), bodies=False, minutes=1)   # nothing to remove here
    outcome = _archive(sid)
    assert outcome["freed"] == {
        "requests": 1, "request_body_bytes": 4 * len(BODY) + len("[]"), "content_rows": 2, "content_bytes": len("alpha") + len("beta"),
    }


def test_active_sessions_cannot_be_archived_and_unknown_ones_are_reported(db_path):
    active = _session("live", ended=False)
    with get_db() as db:
        with pytest.raises(session_archive.SessionStillActive):
            session_archive.archive_session_data(db, active)
        with pytest.raises(session_archive.SessionNotFound):
            session_archive.archive_session_data(db, "nope")


def test_content_shared_with_other_sessions_or_unassigned_requests_is_kept(db_path):
    old = _session("old")
    other = _session("other", ended=False)
    _request("o1", old, texts=("only-old", "shared-with-other", "shared-with-loose"))
    _request("x1", other, texts=("shared-with-other",), minutes=1)
    _request("loose", None, texts=("shared-with-loose",), minutes=2)

    outcome = _archive(old)

    assert _contents() == {"shared-with-other", "shared-with-loose"}
    assert outcome["freed"]["content_rows"] == 1
    # The other session is untouched.
    assert all(value is not None for value in _row("x1").values())


def test_shared_content_goes_once_the_last_non_archived_user_is_archived(db_path):
    a = _session("a")
    b = _session("b")
    _request("ra", a, texts=("shared", "a-only"))
    _request("rb", b, texts=("shared",), minutes=1)
    _archive(a)
    assert _contents() == {"shared"}                       # b still needs it
    outcome = _archive(b)
    assert _contents() == set() and outcome["freed"]["content_rows"] == 1


def test_archiving_twice_is_idempotent_and_purges_stragglers(db_path):
    sid = _session("old")
    _request("a", sid, texts=("alpha",))
    first = _archive(sid)
    stamp = first["session"]["archived_at"]
    # A request that was still in flight when the session ended completes afterwards.
    _request("late", sid, texts=("late-text",), minutes=5)

    again = _archive(sid)

    assert again["already_archived"] is True and again["session"]["archived_at"] == stamp
    assert again["freed"]["requests"] == 1 and again["freed"]["content_rows"] == 1
    assert all(value is None for value in _row("late").values()) and _contents() == set()


def test_a_second_archive_of_the_same_session_is_rejected_while_one_runs(db_path):
    sid = _session("old")
    session_archive._in_progress.add(sid)
    try:
        with get_db() as db:
            with pytest.raises(session_archive.ArchiveInProgress):
                session_archive.archive_session_data(db, sid)
    finally:
        session_archive._in_progress.discard(sid)


def test_analysis_results_are_the_same_before_and_after(db_path):
    sid = _session("old")
    _request("a", sid, texts=("alpha", "beta"), session_seq=1)
    _request("b", sid, texts=("alpha", "beta", "gamma"), minutes=1, session_seq=2, predecessor_response_id="a")
    with get_db() as db:
        block_id = db.query(BlockRecord).filter(BlockRecord.request_id == "b").first().id
        occurrences_before = block_occurrence_service.get_block_occurrences(db, "b", block_id, "session")
        stats_before = crud.get_stats(db, sid)
        graph_before = crud.get_session_lineage_graph(db, sid)["edges"]

    _archive(sid)

    with get_db() as db:
        assert block_occurrence_service.get_block_occurrences(db, "b", block_id, "session") == occurrences_before
        assert crud.get_stats(db, sid) == stats_before
        assert crud.get_session_lineage_graph(db, sid)["edges"] == graph_before


# --------------------------------------------------------------------------- race with capture

def test_content_that_a_capture_starts_using_between_chunks_is_not_deleted(db_path, monkeypatch):
    """A capture in another session adds a block for a hash that archive has not reached yet."""
    sid = _session("old")
    other = _session("other", ended=False)
    texts = tuple(f"text-{i:03d}" for i in range(6))
    _request("a", sid, texts=texts)
    _request("c", other, texts=("seed",), minutes=1)
    monkeypatch.setattr(session_archive, "HASH_CHUNK", 2)
    real = session_archive._chunks
    state = {"calls": 0}

    def chunks_with_a_capture_in_the_middle(values, size):
        for chunk in real(values, size):
            if state["calls"] == 1:
                # After the first chunk was cleaned: capture-style insert (content, then block) for a hash of a later chunk.
                with get_db() as db:
                    late = sorted(values)[-1]
                    content = next(t for t in texts if Block.make(Direction.INPUT, BlockType.USER_MESSAGE, t).content_hash == late)
                    crud.insert_blocks(db, "c", [Block.make(Direction.INPUT, BlockType.USER_MESSAGE, content, message_index=9)])
                state["kept"] = content
            state["calls"] += 1
            yield chunk

    monkeypatch.setattr(session_archive, "_chunks", chunks_with_a_capture_in_the_middle)
    _archive(sid)
    assert state["calls"] >= 3
    assert state["kept"] in _contents()                     # still there for the other session's block
    with get_db() as db:
        assert any(b["content"] == state["kept"] for b in crud.get_blocks(db, "c"))


# --------------------------------------------------------------------------- file space

def test_archive_in_an_incremental_database_shrinks_the_file(db_path):
    sid = _session("old")
    for index in range(40):
        _request(f"r{index}", sid, texts=(prose(150_000, index) + f"-{index}",), minutes=index)
    engine = database.get_engine()
    with engine.connect() as c:
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    before = os.path.getsize(db_path)

    _archive(sid)
    space = session_archive.reclaim_space(engine)

    assert space["auto_vacuum"] == "incremental" and space["reclaimed_bytes"] > 1_000_000
    assert space["free_bytes_remaining"] == 0 and space["note"] is None
    with engine.connect() as c:
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    assert os.path.getsize(db_path) < before / 2


def test_archive_in_a_database_without_auto_vacuum_explains_how_to_shrink_it(tmp_path):
    path = tmp_path / "legacy.db"
    init_db(path)
    database.dispose_engine()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA auto_vacuum = NONE")
    conn.execute("VACUUM")
    conn.close()
    init_db(path)
    try:
        sid = _session("old")
        _request("a", sid, texts=("alpha",))
        _archive(sid)
        space = session_archive.reclaim_space(database.get_engine())
    finally:
        database.dispose_engine()
    assert space["auto_vacuum"] == "none" and space["reclaimed_bytes"] == 0 and "db-compact" in space["note"]


def test_the_vacuum_step_respects_its_time_budget(db_path):
    sid = _session("old")
    for index in range(5):
        _request(f"r{index}", sid, texts=(prose(200_000, index) + str(index),), minutes=index)
    _archive(sid)
    space = session_archive.reclaim_space(database.get_engine(), budget_seconds=0.0)
    assert space["auto_vacuum"] == "incremental" and space["free_bytes_remaining"] > 0 and "time limit" in space["note"]


# --------------------------------------------------------------------------- request payloads

def test_content_state_tells_retained_archived_and_never_stored_apart(db_path):
    sid = _session("old")
    _request("kept", sid, texts=("alpha",))
    _request("bare", sid, texts=("beta",), bodies=False, minutes=1)
    with get_db() as db:
        assert db.get(Request, "kept").to_dict()["content_state"] == "retained"
        assert db.get(Request, "bare").to_dict()["content_state"] == "not_retained"
        assert "content_state" not in db.get(Request, "kept").to_dict(include_raw=False)
    _archive(sid)
    with get_db() as db:
        archived = db.get(Request, "kept").to_dict()
        assert archived["content_state"] == "archived" and archived["session_archived_at"]
        assert db.get(Request, "bare").to_dict()["content_state"] == "archived"
    _request("loose", None, texts=("gamma",), bodies=False, minutes=2)
    with get_db() as db:
        assert db.get(Request, "loose").to_dict()["content_state"] == "not_retained"


def test_session_status_in_every_representation(db_path):
    active = _session("live", ended=False)
    ended = _session("done")
    archived = _session("old")
    _archive(archived)
    with get_db() as db:
        assert {s.id: s.to_dict()["status"] for s in crud.list_sessions(db)} == {active: "active", ended: "ended", archived: "archived"}
        summary = {e["session_id"]: e for e in crud.get_sessions_summary(db) if e["type"] == "session"}
    assert (summary[active]["status"], summary[ended]["status"], summary[archived]["status"]) == ("active", "ended", "archived")
    assert summary[archived]["archived_at"] and summary[ended]["archived_at"] is None


# --------------------------------------------------------------------------- HTTP

class _FakeWs:
    loop = object()

    def broadcast(self, message):
        return message


@pytest.fixture
def client(db_path, monkeypatch):
    sent = []
    monkeypatch.setattr(sessions_router, "_get_ws", lambda: _FakeWs())
    monkeypatch.setattr(sessions_router.asyncio, "run_coroutine_threadsafe", lambda message, loop: sent.append(message))
    app = FastAPI()
    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(requests_router.router, prefix="/api")
    test_client = TestClient(app)
    test_client.sent = sent
    return test_client


def test_archive_endpoint_success_repeat_and_errors(client):
    sid = _session("old")
    _request("a", sid, texts=("alpha",))
    assert client.post("/api/sessions/missing/archive").status_code == 404
    assert client.post(f"/api/sessions/{_session('live', ended=False)}/archive").status_code == 409

    body = client.post(f"/api/sessions/{sid}/archive").json()
    assert body["session"]["status"] == "archived" and body["already_archived"] is False
    assert body["freed"]["requests"] == 1 and body["freed"]["content_rows"] == 1
    assert body["space"]["auto_vacuum"] == "incremental"
    assert client.sent == [{"event": "session_archived", "data": body["session"]}]

    again = client.post(f"/api/sessions/{sid}/archive").json()
    assert again["already_archived"] is True and again["freed"]["requests"] == 0
    assert client.get(f"/api/requests/a").json()["request"]["content_state"] == "archived"
    assert client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "archived"

    session_archive._in_progress.add(sid)
    try:
        assert client.post(f"/api/sessions/{sid}/archive").status_code == 409
    finally:
        session_archive._in_progress.discard(sid)


# --------------------------------------------------------------------------- retention defaults

def test_time_based_purge_is_off_by_default_and_a_notice_is_logged_only_when_enabled(db_path, caplog):
    assert (RetentionSettings().raw_body_days, RetentionSettings().block_content_days) == (0, 0)
    assert "raw_body_days = 0" in Settings(config_dir=db_path.parent).__dict__ or True
    off = Settings(config_dir=db_path.parent)
    with caplog.at_level(logging.INFO, logger="contextspy.db.database"):
        database.startup_vacuum(off)
    assert "Time-based purge" not in caplog.text

    on = Settings(config_dir=db_path.parent)
    on.retention.raw_body_days = 3
    with caplog.at_level(logging.INFO, logger="contextspy.db.database"):
        database.startup_vacuum(on)
    assert "Time-based purge is enabled" in caplog.text and "session archive" in caplog.text


def test_an_explicit_retention_setting_is_still_honoured(db_path):
    sid = _session("old")
    old = T0 - timedelta(days=30)
    _request("a", sid, texts=("alpha",))
    with get_db() as db:
        db.get(Request, "a").timestamp = old
    settings = Settings(config_dir=db_path.parent)
    settings.retention.raw_body_days = 7
    database.startup_vacuum(settings)
    assert all(value is None for value in _row("a").values())


def test_the_generated_config_template_uses_the_new_default(tmp_path):
    settings = Settings(config_dir=tmp_path)
    settings.write_defaults()
    text = (tmp_path / "config.toml").read_text()
    assert "raw_body_days = 0" in text and "block_content_days = 0" in text


# --------------------------------------------------------------------------- resuming a conversation (documented behaviour)

def test_a_resumed_conversation_loses_its_stored_context_when_the_predecessor_was_archived(db_path):
    pytest.importorskip("mitmproxy")
    from contextspy.proxy.addon import _DatabaseLineageRepository

    sid = _session("old")
    document = '{"model": "m", "input": "hello"}'
    _request("resp-1", sid, texts=("hello",), canonical_request_body=document, canonical_response_body='{"id": "resp-1", "output": []}')
    repository = _DatabaseLineageRepository()
    assert repository.get("anthropic", "resp-1") is not None
    _archive(sid)
    # No stored body to rebuild the context from: the continuation will be captured with partial context.
    assert repository.get("anthropic", "resp-1") is None


# --------------------------------------------------------------------------- CLI

class _Reply:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, str(payload)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


@pytest.fixture
def cli_api(monkeypatch):
    calls = {"posts": []}
    sessions = [
        {"id": "aaaa1111", "name": "old one", "is_active": False, "status": "ended", "started_at": "2026-10-01T00:00:00", "ended_at": "2026-10-01T01:00:00"},
        {"id": "aaaa2222", "name": "twin", "is_active": False, "status": "ended", "started_at": "2026-10-02T00:00:00", "ended_at": None},
        {"id": "bbbb1111", "name": "live", "is_active": True, "status": "active", "started_at": "2026-10-03T00:00:00", "ended_at": None},
        {"id": "cccc1111", "name": "done", "is_active": False, "status": "archived", "started_at": "2026-10-04T00:00:00", "ended_at": "2026-10-04T01:00:00"},
    ]
    result = {
        "session": {}, "already_archived": False,
        "freed": {"requests": 3, "request_body_bytes": 3 * 1024 * 1024, "content_rows": 5, "content_bytes": 2048},
        "space": {"auto_vacuum": "incremental", "reclaimed_bytes": 4 * 1024 * 1024, "free_bytes_remaining": 0, "note": None},
    }
    monkeypatch.setattr(cli, "_web_port", lambda: 5173)
    monkeypatch.setattr(cli.httpx, "get", lambda url, **kw: _Reply({"sessions": sessions}))

    def post(url, **kw):
        calls["posts"].append((url, kw))
        return _Reply(calls.get("reply", result), calls.get("status", 200))

    monkeypatch.setattr(cli.httpx, "post", post)
    calls["result"] = result
    return calls


def test_cli_archive_resolves_a_prefix_confirms_and_uses_a_long_timeout(cli_api):
    from typer.testing import CliRunner

    runner = CliRunner()
    declined = runner.invoke(cli.app, ["session", "archive", "aaaa1"], input="n\n")
    assert declined.exit_code == 1 and not cli_api["posts"]
    done = runner.invoke(cli.app, ["session", "archive", "aaaa1", "--yes"])
    assert done.exit_code == 0, done.output
    assert "Session archived" in done.output and "3.0 MiB of payloads from 3 requests" in done.output and "File space returned: 4.0 MiB" in done.output
    url, kwargs = cli_api["posts"][-1]
    assert url.endswith("/sessions/aaaa1111/archive") and kwargs["timeout"] >= 300


def test_cli_archive_refuses_ambiguous_unknown_and_active_sessions(cli_api):
    from typer.testing import CliRunner

    runner = CliRunner()
    assert "More than one" in runner.invoke(cli.app, ["session", "archive", "aaaa", "--yes"]).output
    assert "No session" in runner.invoke(cli.app, ["session", "archive", "zzzz", "--yes"]).output
    active = runner.invoke(cli.app, ["session", "archive", "bbbb", "--yes"])
    assert active.exit_code == 1 and "still active" in active.output
    assert not cli_api["posts"]


def test_cli_archive_reports_server_errors_and_notes(cli_api):
    from typer.testing import CliRunner

    runner = CliRunner()
    cli_api.update(reply={"detail": "End the session before archiving it"}, status=409)
    failed = runner.invoke(cli.app, ["session", "archive", "aaaa1", "--yes"])
    assert failed.exit_code == 1 and "End the session before archiving it" in failed.output
    cli_api.pop("reply"); cli_api.pop("status")
    cli_api["result"]["space"] = {"auto_vacuum": "none", "reclaimed_bytes": 0, "free_bytes_remaining": 10, "note": "run db-compact"}
    cli_api["reply"] = cli_api["result"]
    noted = runner.invoke(cli.app, ["session", "archive", "aaaa1", "--yes"])
    assert noted.exit_code == 0 and "run db-compact" in noted.output and "File space returned" not in noted.output


def test_cli_session_list_shows_the_status(cli_api):
    from typer.testing import CliRunner

    output = CliRunner().invoke(cli.app, ["session", "list"]).output
    assert "Status" in output and "archived" in output and "active" in output and "ended" in output
