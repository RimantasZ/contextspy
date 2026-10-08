from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession

from contextspy.api.routers import sessions as sessions_router
from contextspy.analysis.accounting import cached_share_pct
from contextspy.db import crud, database, trend_service
from contextspy.db.models import Base, Request, Session

T0 = datetime(2026, 9, 18, 8, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with OrmSession(engine) as session:
        yield session


def _session(db, sid="s1"):
    db.add(Session(id=sid, name=sid, started_at=T0, is_active=1))
    db.flush()


def _req(db, rid, *, sid="s1", seq=1, tin=100, parent=None, started_at=None, **extra):
    r = Request(
        id=rid, session_id=sid, session_seq=seq, timestamp=T0 + timedelta(minutes=seq or 0),
        provider="anthropic", endpoint="/v1/messages", tokens_total_input=tin,
        tokens_total_output=5, provider_response_id=rid, predecessor_response_id=parent,
        started_at=started_at, **extra,
    )
    db.add(r)
    db.flush()
    return r


def _ids(series):
    return [p["request_id"] for p in series["points"]]


def test_metric_registry_is_described_in_response(db):
    _session(db)
    out = crud.get_session_trend(db, "s1")
    metrics = {m["id"]: m for m in out["metrics"]}
    assert list(metrics) == ["context_estimated", "cache_hit_pct", "ttft_ms", "duration_ms"]
    assert metrics["context_estimated"]["estimated"] is True
    assert metrics["context_estimated"]["empty_hint"] is None
    assert metrics["cache_hit_pct"]["unit"] == "percent" and metrics["cache_hit_pct"]["empty_hint"]
    assert out["series"] == []


def test_values_per_request(db):
    _session(db)
    _req(db, "r1", seq=1, tin=1234, provider_input_tokens=2000, cache_read_tokens=500,
         ttft_ms=640, duration_ms=5100, purpose="main")
    series = crud.get_session_trend(db, "s1")["series"]
    (point,) = [p for s in series for p in s["points"]]
    assert point["values"] == {
        "context_estimated": 1234, "cache_hit_pct": 25.0, "ttft_ms": 640, "duration_ms": 5100,
    }
    assert point["purpose"] == "main" and point["session_seq"] == 1


def test_missing_provider_usage_and_timing_are_null(db):
    _session(db)
    _req(db, "r1", seq=1, tin=50)
    _req(db, "r2", seq=2, tin=60, provider_input_tokens=0, cache_read_tokens=0)
    values = [p["values"] for s in crud.get_session_trend(db, "s1")["series"] for p in s["points"]]
    assert all(v["cache_hit_pct"] is None and v["ttft_ms"] is None and v["duration_ms"] is None
               for v in values)
    assert [v["context_estimated"] for v in values] == [50, 60]


@pytest.mark.parametrize("read,total,expected", [
    (None, 100, 0.0), (50, 100, 50.0), (0, 100, 0.0), (10, None, None), (10, 0, None),
])
def test_cached_share_matches_request_detail(db, read, total, expected):
    _session(db)
    r = _req(db, "r1", provider_input_tokens=total, cache_read_tokens=read)
    assert cached_share_pct(read, total) == expected
    assert r.to_dict()["context_accounting"]["cached_share_pct"] == expected
    point = trend_service._point(r, 1)
    assert point["values"]["cache_hit_pct"] == expected


def test_time_prefers_started_at_and_fidelity_is_exposed(db):
    _session(db)
    started = T0 - timedelta(hours=1)
    _req(db, "r1", seq=1, started_at=started, context_fidelity="partial")
    _req(db, "r2", seq=2)
    points = [p for s in crud.get_session_trend(db, "s1")["series"] for p in s["points"]]
    by_id = {p["request_id"]: p for p in points}
    assert by_id["r1"]["time"] == started.isoformat()
    assert by_id["r1"]["context_fidelity"] == "partial"
    assert by_id["r2"]["time"] == (T0 + timedelta(minutes=2)).isoformat()
    assert by_id["r2"]["context_fidelity"] == "complete"


def test_fork_plots_shared_history_once_in_first_conversation(db):
    _session(db)
    _req(db, "root", seq=1)
    _req(db, "a", seq=2, parent="root")
    _req(db, "b", seq=3, parent="root")
    _req(db, "aa", seq=4, parent="a")
    _req(db, "bb", seq=5, parent="b")
    series = crud.get_session_trend(db, "s1")["series"]
    conversations = [s for s in series if not s["auxiliary"]]
    assert len(conversations) == 2
    all_ids = [rid for s in series for rid in _ids(s)]
    assert sorted(all_ids) == sorted(["root", "a", "b", "aa", "bb"])
    assert all(s["request_count"] == len(s["points"]) for s in series)
    for s in series:
        seqs = [p["session_seq"] for p in s["points"]]
        assert seqs == sorted(seqs)


def test_unlinked_requests_are_auxiliary(db):
    _session(db)
    for seq in range(1, 4):
        _req(db, f"x{seq}", seq=seq)
    series = crud.get_session_trend(db, "s1")["series"]
    assert [s["auxiliary"] for s in series] == [True]
    assert _ids(series[0]) == ["x1", "x2", "x3"]


def test_external_ids_dropped_and_ungrouped_requests_go_to_auxiliary(db):
    _session(db)
    _session(db, "other")
    _req(db, "mine1", seq=1)
    _req(db, "mine2", seq=2)
    _req(db, "theirs", sid="other", seq=1)
    graph = {
        "conversations": [{"key": "c1", "label": "Conversation 1", "request_ids": ["theirs", "mine1"]}],
        "auxiliary": None,
    }
    out = trend_service.build_session_trend(db, "s1", graph)
    assert [(s["key"], s["auxiliary"], _ids(s)) for s in out["series"]] == [
        ("c1", False, ["mine1"]),
        ("session:s1:auxiliary", True, ["mine2"]),
    ]


def test_duplicate_across_groups_and_aux_claimed_once(db):
    _session(db)
    for seq in (1, 2, 3):
        _req(db, f"r{seq}", seq=seq)
    graph = {
        "conversations": [
            {"key": "c1", "label": "C1", "request_ids": ["r1", "r2"]},
            {"key": "c2", "label": "C2", "request_ids": ["r2", "r3"]},
        ],
        "auxiliary": {"key": "aux", "label": "Auxiliary requests", "request_ids": ["r1", "r3"]},
    }
    out = trend_service.build_session_trend(db, "s1", graph)
    assert [(s["key"], _ids(s)) for s in out["series"]] == [("c1", ["r1", "r2"]), ("c2", ["r3"])]


def test_null_session_seq_orders_last_then_by_time(db):
    _session(db)
    _req(db, "n", seq=None)
    _req(db, "b", seq=2)
    _req(db, "a", seq=1)
    graph = {"conversations": [], "auxiliary": None}
    out = trend_service.build_session_trend(db, "s1", graph)
    assert _ids(out["series"][0]) == ["a", "b", "n"]
    assert out["series"][0]["points"][2]["session_seq"] is None
    assert [p["ordinal"] for p in out["series"][0]["points"]] == [1, 2, 3]


def test_route_returns_trend_and_404(tmp_path):
    database.init_db(tmp_path / "trend.db")
    try:
        app = FastAPI()
        app.include_router(sessions_router.router, prefix="/api")
        client = TestClient(app)
        assert client.get("/api/sessions/nope/trend").status_code == 404
        with database.get_db() as session:
            _session(session)
            _req(session, "r1", seq=1, tin=7)
        response = client.get("/api/sessions/s1/trend")
        assert response.status_code == 200
        assert response.json()["series"][0]["points"][0]["values"]["context_estimated"] == 7
    finally:
        database.dispose_engine()
