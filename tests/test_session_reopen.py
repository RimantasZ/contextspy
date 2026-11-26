"""Reopening the last closed session (issue #106)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from contextspy import cli
from contextspy.api.routers import sessions as sessions_router
from contextspy.db import crud, database, session_archive
from contextspy.db.database import get_db, init_db
from contextspy.db.models import Request, Session

T0 = datetime(2026, 11, 1, 8, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "reopen.db"
    init_db(path)
    yield path
    database.dispose_engine()


class _FakeWs:
    loop = object()

    def broadcast(self, message):
        return message


@pytest.fixture
def api(db_path, monkeypatch):
    sent = []
    monkeypatch.setattr(sessions_router, "_get_ws", lambda: _FakeWs())
    monkeypatch.setattr(sessions_router.asyncio, "run_coroutine_threadsafe", lambda message, loop: sent.append(message))
    app = FastAPI()
    app.include_router(sessions_router.router, prefix="/api")
    client = TestClient(app)
    client.sent = sent
    return client


def _session(name, start_hour, end_hour=None, *, archived=False):
    with get_db() as db:
        session = crud.create_session(db, name)
        session.started_at = T0 + timedelta(hours=start_hour)
        if end_hour is not None:
            session.ended_at = T0 + timedelta(hours=end_hour)
            session.is_active = 0
        if archived:
            session.archived_at = T0 + timedelta(hours=end_hour)
        return session.id


def _request(request_id, session_id, hour):
    with get_db() as db:
        crud.create_request(db, {
            "id": request_id, "session_id": session_id, "timestamp": T0 + timedelta(hours=hour),
            "provider": "anthropic", "endpoint": "/v1/messages",
            "tokens_total_input": 10, "tokens_total_output": 2,
        })


def _active():
    with get_db() as db:
        return [s.id for s in crud.list_sessions(db) if s.is_active]


def _get(session_id):
    with get_db() as db:
        return crud.get_session(db, session_id).to_dict()


def test_reopen_continues_the_session_and_its_numbering(api):
    sid = _session("work", 0, 1)
    _request("before", sid, 0.5)
    _request("between", None, 2)
    assert api.get("/api/sessions").json()["reopenable_session_id"] == sid
    assert api.get(f"/api/sessions/{sid}").json()["reopenable_session_id"] == sid

    body = api.post(f"/api/sessions/{sid}/reopen").json()
    assert body["warning"] is None
    assert body["session"]["status"] == "active" and body["session"]["ended_at"] is None
    assert body["session"]["started_at"].startswith("2026-11-01T08:00")
    assert [m["event"] for m in api.sent] == ["session_reopened"]
    _request("after", sid, 3)
    with get_db() as db:
        assert db.get(Request, "after").session_seq == db.get(Request, "before").session_seq + 1
        assert db.get(Request, "between").session_id is None
    assert api.get("/api/sessions").json()["reopenable_session_id"] is None


def test_reopen_ends_the_active_session_and_can_switch_back(api):
    first = _session("first", 0, 1)
    second = _session("second", 2)
    _request("in-second", second, 3)

    body = api.post(f"/api/sessions/{first}/reopen").json()
    assert "'second'" in body["warning"]
    assert _active() == [first]
    assert [m["event"] for m in api.sent] == ["session_ended", "session_reopened"]
    with get_db() as db:
        assert db.get(Request, "in-second").session_id == second
    assert api.get("/api/sessions").json()["reopenable_session_id"] == second

    assert api.post(f"/api/sessions/{second}/reopen").status_code == 200
    assert _active() == [second]


def test_refused_reopen_changes_nothing(api):
    older = _session("older", 0, 1)
    last = _session("last", 2, 3)
    live = _session("live", 4)

    assert api.post("/api/sessions/nope/reopen").status_code == 404
    assert "Only the last closed" in api.post(f"/api/sessions/{older}/reopen").json()["detail"]
    assert "already active" in api.post(f"/api/sessions/{live}/reopen").json()["detail"]
    with session_archive._in_progress_lock:
        session_archive._in_progress.add(last)
    try:
        busy = api.post(f"/api/sessions/{last}/reopen")
        assert busy.status_code == 409 and "being archived" in busy.json()["detail"]
    finally:
        session_archive._in_progress.discard(last)
    assert _active() == [live] and not api.sent


def test_an_archived_last_session_has_no_fallback(api):
    older = _session("older", 0, 1)
    archived = _session("archived", 2, 3, archived=True)
    assert api.get("/api/sessions").json()["reopenable_session_id"] is None
    assert "archived" in api.post(f"/api/sessions/{archived}/reopen").json()["detail"]
    assert api.post(f"/api/sessions/{older}/reopen").status_code == 409

    with get_db() as db:
        crud.delete_session(db, archived)
    assert api.get("/api/sessions").json()["reopenable_session_id"] == older


def test_archive_stops_if_the_session_was_reopened_after_its_first_check(db_path, monkeypatch):
    sid = _session("work", 0, 1)
    _request("r", sid, 0.5)
    with get_db() as db:
        db.get(Request, "r").raw_request_body = "payload"

    real_lock = session_archive._in_progress_lock

    class ReopenFirst:
        """Reopens the session just before archive registers it."""
        done = False

        def __enter__(self):
            if not self.done:
                self.done = True
                with get_db() as other:
                    crud.reopen_session(other, sid)
            return real_lock.__enter__()

        def __exit__(self, *args):
            return real_lock.__exit__(*args)

    monkeypatch.setattr(session_archive, "_in_progress_lock", ReopenFirst())
    with pytest.raises(session_archive.SessionStillActive):
        with get_db() as db:
            session_archive.archive_session_data(db, sid)
    with get_db() as db:
        assert db.get(Request, "r").raw_request_body == "payload"
        assert db.get(Session, sid).archived_at is None
    assert sid not in session_archive._in_progress


def _timeline():
    with get_db() as db:
        entries = crud.get_sessions_summary(db)
    return [(e["type"], e["name"], e["request_count"]) for e in entries]


def test_timeline_keeps_unassigned_requests_around_a_reopened_session(db_path):
    first = _session("first", 1, 2)
    _request("lead", None, 0)
    _request("closed-1", None, 3)
    _session("second", 4, 5)
    _request("closed-2", None, 6)
    assert _timeline() == [("gap", None, 1), ("session", "second", 0), ("gap", None, 1),
                           ("session", "first", 0), ("gap", None, 1)]
    before = _timeline()
    with get_db() as db:
        # As if "first" had been closed last, so that it now spans "second" once reopened.
        db.get(Session, first).ended_at = T0 + timedelta(hours=7)
    with get_db() as db:
        crud.reopen_session(db, first)
    assert _timeline() == before  # overlapping "second": every unassigned request still listed once


def test_cli_reopen(monkeypatch):
    listing = {"sessions": [], "reopenable_session_id": "abcd1234efgh"}
    posts = []

    class Reply:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            pass

    monkeypatch.setattr(cli, "_web_port", lambda: 5173)
    monkeypatch.setattr(cli.httpx, "get", lambda url, **kw: Reply(listing))

    def post(url, **kw):
        posts.append(url)
        return Reply({"session": {"id": "abcd1234efgh", "name": "work"},
                      "warning": "Previous session 'x' was automatically ended."})

    monkeypatch.setattr(cli.httpx, "post", post)
    done = CliRunner().invoke(cli.app, ["session", "reopen"])
    assert done.exit_code == 0, done.output
    assert "Session reopened" in done.output and "automatically ended" in done.output
    assert posts[0].endswith("/sessions/abcd1234efgh/reopen")

    listing["reopenable_session_id"] = None
    nothing = CliRunner().invoke(cli.app, ["session", "reopen"])
    assert "Nothing to reopen" in nothing.output and len(posts) == 1
