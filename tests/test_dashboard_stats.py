from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession

from contextspy.api.routers import stats as stats_router, sessions as sessions_router
from contextspy.db import crud, database
from contextspy.db.models import Base, BlockRecord, Request, Session

T0 = datetime(2026, 9, 18, 8, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with OrmSession(engine) as session:
        yield session


def _session(db, sid="s1", *, active=True):
    s = Session(id=sid, name=f"name-{sid}", started_at=T0, is_active=1 if active else 0)
    db.add(s)
    db.flush()
    return s


def _req(db, rid, *, sid="s1", seq=1, tin=100, tout=10, minutes=None, fidelity="complete", parent=None, started_at=None, hint=None):
    r = Request(
        id=rid,
        session_id=sid,
        session_seq=seq,
        timestamp=T0 + timedelta(minutes=seq if minutes is None else minutes),
        provider="openai_chatgpt" if hint else "anthropic",
        endpoint="/backend-api/codex/responses" if hint else "/v1/messages",
        agent="codex" if hint else None,
        stream_hint_source="openai_prompt_cache_key" if hint else None,
        stream_hint_digest=hint,
        tokens_total_input=tin,
        tokens_total_output=tout,
        context_fidelity=fidelity,
        provider_response_id=rid,
        predecessor_response_id=parent,
        started_at=started_at,
    )
    db.add(r)
    db.flush()
    return r


def _blocks(db, rid, block_type, n, direction="input", content_hash=None):
    for i in range(n):
        db.add(BlockRecord(
            request_id=rid, direction=direction, position=i,
            block_type=block_type, content_hash=content_hash, token_count=0,
        ))
    db.flush()


def _stream_blocks(db, rid, family):
    for index in range(3):
        db.add(BlockRecord(
            request_id=rid, direction="input", position=index,
            block_type="tool_result", category="tool_results",
            content_hash=f"{family}-{index}", token_count=50,
        ))
    db.flush()


def test_no_active_session_returns_empty_contract(db):
    _session(db, active=False)
    _req(db, "r1", sid="s1")
    out = crud.get_dashboard_live(db)
    assert out["active_session"] is None and out["conversations"] == []
    assert out["conversation_count"] == out["lineage_fragment_count"] == 0


def test_active_session_without_requests(db):
    _session(db)
    out = crud.get_dashboard_live(db)
    assert out["active_session"] == {
        "id": "s1", "name": "name-s1", "started_at": T0.isoformat(),
        "request_count": 0, "tokens_total_input": 0, "tokens_total_output": 0,
    }
    assert out["request_flow"] == [] and out["activity"] == []
    assert out["context_change"] is None


def test_excludes_other_sessions_and_sums_totals(db):
    _session(db)
    _session(db, "old", active=False)
    _req(db, "a", seq=1, tin=100, tout=5)
    _req(db, "b", seq=2, tin=250, tout=7)
    _req(db, "old1", sid="old", seq=1, tin=9999, tout=9999)
    _req(db, "ungrouped", sid=None, seq=None, tin=8888, tout=8888, minutes=3)
    out = crud.get_dashboard_live(db)
    assert out["active_session"]["request_count"] == 2
    assert out["active_session"]["tokens_total_input"] == 350
    assert out["active_session"]["tokens_total_output"] == 12
    ids = {r["id"] for r in out["request_flow"]} | {r["id"] for r in out["activity"]}
    assert ids == {"a", "b"}


def test_flow_limit_and_order_and_activity_limit_and_order(db):
    _session(db)
    for i in range(1, 13):
        _req(db, f"r{i}", seq=i)
    out = crud.get_dashboard_live(db)
    assert [r["session_seq"] for r in out["request_flow"]] == [12, 11, 10, 9, 8]
    assert [r["session_seq"] for r in out["activity"]] == list(range(3, 13))
    assert out["active_session"]["request_count"] == 12
    assert set(out["request_flow"][0]) == {
        "id", "session_seq", "timestamp", "model", "duration_ms",
        "status_code", "invocation_outcome", "tokens_total_input", "tokens_total_output",
    }


def test_dashboard_conversation_preview_has_fifteen_cards_and_total_count(db):
    _session(db)
    for seq in range(1, 24):
        _req(db, f"r{seq}", seq=seq, parent=f"r{seq - 1}" if seq > 1 else None)
    dashboard = crud.get_dashboard_live(db)
    group = dashboard["conversations"][0]
    assert group["request_count"] == 23
    assert group["has_older_requests"] is True
    assert [card["session_seq"] for segment in group["recent_segments"]
            for card in segment["request_flow"]] == list(range(23, 8, -1))
    session = crud.get_session_conversations(db, "s1")
    assert session["conversations"] == dashboard["conversations"]


@pytest.mark.parametrize("prev,cur,delta", [(1000, 1600, 600), (1600, 1000, -600), (500, 500, 0)])
def test_token_delta(db, prev, cur, delta):
    _session(db)
    _req(db, "p", seq=1, tin=prev)
    _req(db, "c", seq=2, tin=cur, parent="p")
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["token_delta"] == delta
    assert cc["tokens_total_input"] == cur
    assert cc["parent_request_id"] == "p" and cc["parent_session_seq"] == 1
    assert cc["request_id"] == "c" and cc["session_seq"] == 2


def test_first_request(db):
    _session(db)
    _req(db, "only", seq=1, tin=42)
    out = crud.get_dashboard_live(db)
    assert out["request_flow"][0]["id"] == "only" and out["activity"][0]["id"] == "only"
    cc = out["context_change"]
    assert cc["parent_request_id"] is None
    assert cc["parent_session_seq"] is None
    assert cc["token_delta"] is None
    assert cc["comparison_fidelity"] == "unavailable"
    assert cc["block_changes"] == []


def test_previous_is_same_session_despite_newer_global_request(db):
    _session(db)
    _session(db, "other", active=False)
    _req(db, "p", seq=1, tin=100)
    _req(db, "c", seq=2, tin=300, parent="p")
    _req(db, "newer-global", sid="other", seq=7, tin=5, minutes=500)
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["request_id"] == "c" and cc["parent_request_id"] == "p"
    assert cc["token_delta"] == 200


def test_block_changes_input_only_with_counts_and_zeros(db):
    _session(db)
    _req(db, "p", seq=1)
    _req(db, "c", seq=2, parent="p")
    _blocks(db, "p", "tool_call", 2)
    _blocks(db, "p", "tool_result", 3)
    _blocks(db, "p", "user_message", 1)
    _blocks(db, "p", "assistant_message", 4, direction="output")
    _blocks(db, "c", "tool_call", 4)
    _blocks(db, "c", "tool_result", 1)
    _blocks(db, "c", "user_message", 1)
    _blocks(db, "c", "thinking", 5, direction="output")
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["comparison_fidelity"] == "complete"
    changes = {b["block_type"]: b for b in cc["block_changes"]}
    assert set(changes) == {"tool_call", "tool_result", "user_message"}
    assert changes["tool_call"] == {
        "block_type": "tool_call", "current_count": 4, "previous_count": 2, "delta": 2,
    }
    assert changes["tool_result"]["delta"] == -2
    assert changes["user_message"] == {
        "block_type": "user_message", "current_count": 1, "previous_count": 1, "delta": 0,
    }


def test_type_only_in_previous_is_reported(db):
    _session(db)
    _req(db, "p", seq=1)
    _req(db, "c", seq=2, parent="p")
    _blocks(db, "p", "tool_definition", 3)
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["block_changes"] == [
        {"block_type": "tool_definition", "current_count": 0, "previous_count": 3, "delta": -3},
    ]


def test_purged_content_still_counted(db):
    _session(db)
    _req(db, "p", seq=1)
    _req(db, "c", seq=2, parent="p")
    _blocks(db, "p", "tool_result", 1, content_hash=None)
    _blocks(db, "c", "tool_result", 3, content_hash="gone-hash")
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["block_changes"][0]["delta"] == 2


@pytest.mark.parametrize("prev,cur,expected", [
    ("complete", "complete", "complete"),
    ("partial", "complete", "partial"),
    ("complete", "partial", "partial"),
    ("partial", "partial", "partial"),
    ("opaque", "complete", "unavailable"),
    ("complete", "opaque", "unavailable"),
    ("opaque", "partial", "unavailable"),
])
def test_fidelity(db, prev, cur, expected):
    _session(db)
    _req(db, "p", seq=1, tin=10, fidelity=prev)
    _req(db, "c", seq=2, tin=25, fidelity=cur, parent="p")
    _blocks(db, "p", "tool_call", 1)
    _blocks(db, "c", "tool_call", 2)
    cc = crud.get_dashboard_live(db)["context_change"]
    assert cc["comparison_fidelity"] == expected
    assert cc["token_delta"] == 15
    assert bool(cc["block_changes"]) == (expected != "unavailable")


def test_null_session_seq_is_deterministic(db):
    _session(db)
    _req(db, "a", seq=None, tin=10, minutes=1)
    _req(db, "b", seq=None, tin=30, minutes=2)
    _req(db, "c", seq=None, tin=70, minutes=2)
    first = crud.get_dashboard_live(db)
    assert crud.get_dashboard_live(db) == first
    assert [r["id"] for r in first["request_flow"]] == ["c", "b", "a"]
    assert [r["id"] for r in first["activity"]] == ["a", "b", "c"]
    assert first["request_flow"][0]["session_seq"] is None
    cc = first["context_change"]
    assert cc["request_id"] == "c" and cc["parent_request_id"] is None
    assert cc["token_delta"] is None


def test_latest_compares_with_resolved_parent_not_adjacent_request(db):
    _session(db)
    _req(db, "parent", seq=1, tin=10)
    for seq in range(2, 12):
        _req(db, f"gap-{seq}", seq=seq, tin=999)
    _req(db, "latest", seq=12, tin=90, parent="parent")
    out = crud.get_dashboard_live(db)
    assert out["context_change"]["parent_request_id"] == "parent"
    assert out["context_change"]["token_delta"] == 80
    assert out["conversation_count"] == 1
    assert out["lineage_fragment_count"] == 11
    assert out["conversations"][0]["unlinked_segment_count"] == 10


def test_sustained_fork_dashboard_has_shared_history_and_parent_comparison(db):
    _session(db)
    _req(db, "root", seq=1, tin=100)
    _req(db, "a", seq=2, tin=120, parent="root")
    _req(db, "b", seq=3, tin=130, parent="root")
    _req(db, "aa", seq=4, tin=140, parent="a")
    _req(db, "bb", seq=5, tin=150, parent="b")
    out = crud.get_dashboard_live(db)
    assert out["conversation_count"] == 2
    assert out["active_session"]["request_count"] == 5
    assert all(any(card["id"] == "root" and card["shared_history"]
                   for segment in group["recent_segments"] for card in segment["request_flow"])
               for group in out["conversations"])
    assert {group["context_change"]["parent_request_id"] for group in out["conversations"]} == {"a", "b"}


def test_conversation_order_uses_latest_completion_not_primary_rank(db):
    _session(db)
    for rid, seq, parent in (
        ("root", 1, None), ("a", 2, "root"), ("aa", 3, "a"),
        ("aaa", 4, "aa"), ("b", 5, "root"), ("bb", 6, "b"),
    ):
        _req(db, rid, seq=seq, parent=parent)
    first = crud.get_session_conversations(db, "s1")
    assert [group["latest_request_id"] for group in first["conversations"]] == ["bb", "aaa"]
    assert first["primary_key"] == first["conversations"][1]["key"]
    assert crud.get_dashboard_live(db)["most_recent_conversation_key"] == first["conversations"][0]["key"]
    selected_key = first["conversations"][0]["key"]
    _req(db, "aaaa", seq=7, parent="aaa")
    second = crud.get_session_conversations(db, "s1")
    assert [group["latest_request_id"] for group in second["conversations"]] == ["aaaa", "bb"]
    assert second["conversations"][1]["key"] == selected_key
    assert crud.get_session_conversations(
        db, "s1", revision=second["revision"], group_key=selected_key,
    )["conversations"][0]["key"] == selected_key
    with pytest.raises(ValueError, match="revision changed"):
        crud.get_session_conversations(db, "s1", revision=first["revision"])


def test_dashboard_preview_does_not_reserve_slot_for_older_primary(db):
    _session(db)
    _req(db, "root", seq=1)
    parent = "root"
    for seq in range(2, 6):
        rid = f"main-{seq}"
        _req(db, rid, seq=seq, parent=parent)
        parent = rid
    for branch in range(4):
        child = f"branch-{branch}"
        _req(db, child, seq=10 + branch * 2, parent="root")
        _req(db, f"leaf-{branch}", seq=11 + branch * 2, parent=child)
    session = crud.get_session_conversations(db, "s1", group_limit=10)
    dashboard = crud.get_dashboard_live(db)
    assert session["conversation_count"] == 5
    assert dashboard["has_more_conversations"] is True
    assert len(dashboard["conversations"]) == 4
    assert session["primary_key"] not in {group["key"] for group in dashboard["conversations"]}
    assert [group["latest_session_seq"] for group in dashboard["conversations"]] == [17, 15, 13, 11]


def test_hint_backfill_invalidates_revision_without_request_append(db):
    _session(db)
    row = _req(db, "r1", seq=1)
    before = crud.get_session_conversations(db, "s1")["revision"]
    row.stream_hint_source = "openai_prompt_cache_key"
    row.stream_hint_digest = "digest"
    db.flush()
    after = crud.get_session_conversations(db, "s1")["revision"]
    assert before != after


def test_external_exact_parent_is_compared_but_not_counted(db):
    _session(db)
    _session(db, "old", active=False)
    _req(db, "external", sid="old", seq=1, tin=200)
    _req(db, "child", seq=1, tin=250, parent="external")
    out = crud.get_dashboard_live(db)
    assert out["active_session"]["request_count"] == 1
    assert out["context_change"]["parent_request_id"] == "external"
    assert out["context_change"]["external_parent"] is True
    assert out["context_change"]["token_delta"] == 50
    assert [card["id"] for group in out["conversations"]
            for segment in group["recent_segments"] for card in segment["request_flow"]] == ["child"]


def test_dashboard_pins_one_read_snapshot_during_append(tmp_path, monkeypatch):
    from sqlalchemy import text
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
    with OrmSession(engine) as writer:
        _session(writer)
        _req(writer, "before", seq=1)
        writer.commit()

    original = crud.get_session_lineage_snapshots
    inserted = False

    def append_between_reads(reader, session_id):
        nonlocal inserted
        if not inserted:
            inserted = True
            with OrmSession(engine) as writer:
                _req(writer, "after", seq=2)
                writer.commit()
        return original(reader, session_id)

    monkeypatch.setattr(crud, "get_session_lineage_snapshots", append_between_reads)
    with OrmSession(engine) as reader:
        out = crud.get_dashboard_live(reader)
    assert inserted
    assert out["active_session"]["request_count"] == 1
    assert out["request_flow"][0]["id"] == "before"
    assert out["conversations"][0]["latest_request_id"] == "before"
    with OrmSession(engine) as reader:
        assert crud.get_dashboard_live(reader)["active_session"]["request_count"] == 2


def test_dashboard_batches_card_and_parent_queries(db):
    _session(db)
    _req(db, "root", seq=1)
    _req(db, "a", seq=2, parent="root")
    _req(db, "b", seq=3, parent="root")
    _req(db, "aa", seq=4, parent="a")
    _req(db, "bb", seq=5, parent="b")
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert crud.get_dashboard_live(db)["conversation_count"] == 2
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) <= 9


def test_dashboard_reuses_analysis_until_request_revision_changes(db, monkeypatch):
    _session(db)
    _req(db, "first", seq=1)
    original = crud.get_session_lineage_snapshots
    calls = 0
    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)
    monkeypatch.setattr(crud, "get_session_lineage_snapshots", counted)
    assert crud.get_dashboard_live(db)["active_session"]["request_count"] == 1
    assert crud.get_dashboard_live(db)["active_session"]["request_count"] == 1
    assert calls == 1
    _req(db, "second", seq=2)
    assert crud.get_dashboard_live(db)["active_session"]["request_count"] == 2
    assert calls == 2


def test_session_conversations_page_all_requests_and_reject_stale_cursor(db):
    _session(db, active=False)
    for seq in range(1, 72):
        _req(db, f"r{seq}", seq=seq, parent=f"r{seq - 1}" if seq > 1 else None)
    first = crud.get_session_conversations(db, "s1")
    dashboard = crud.get_dashboard_live(db)
    assert dashboard["conversations"] == []  # Ended sessions are not dashboard-live.
    assert first["conversation_count"] == 1
    assert first["conversations"][0]["request_count"] == 71
    assert len(first["conversations"][0]["recent_segments"][0]["request_flow"]) == 15
    group = first["conversations"][0]
    page = crud.get_session_conversation_requests(
        db, "s1", group["key"], revision=first["revision"],
        cursor=group["next_request_cursor"],
    )
    ids = [card["id"] for segment in page["segments"] for card in segment["request_flow"]]
    assert ids == [f"r{seq}" for seq in range(56, 6, -1)]
    assert page["continues_earlier"] is True
    tail = crud.get_session_conversation_requests(
        db, "s1", group["key"], revision=first["revision"], cursor=page["next_cursor"],
    )
    assert [card["id"] for segment in tail["segments"] for card in segment["request_flow"]] == [
        f"r{seq}" for seq in range(6, 0, -1)
    ]
    assert tail["next_cursor"] is None
    _req(db, "r72", seq=72, parent="r71")
    with pytest.raises(ValueError, match="revision changed"):
        crud.get_session_conversation_requests(db, "s1", group["key"], revision=first["revision"])


def test_session_projection_matches_dashboard_and_marks_unconfirmed_branch(db):
    _session(db)
    _req(db, "root", seq=1)
    _req(db, "a", seq=2, parent="root")
    _req(db, "b", seq=3, parent="root")
    dashboard = crud.get_dashboard_live(db)
    session = crud.get_session_conversations(db, "s1")
    assert session["conversation_count"] == 1
    assert session["conversations"] == dashboard["conversations"]
    assert session["conversations"][0]["unlinked_segment_count"] == 0
    assert session["conversations"][0]["segment_count"] == 2
    assert any(segment["gap_reason"] == "graph_branch_unconfirmed"
               for segment in session["conversations"][0]["recent_segments"])
    graph, _ = crud._session_lineage_graph(db, "s1", 3)
    assert next(node for node in graph["nodes"] if node["request_id"] == "root")["conversation_fork_status"] == "unconfirmed_graph_branch"


def test_stream_groups_match_dashboard_order_and_preserve_gap_provenance(db):
    _session(db)
    rows = [
        (293, "B", "beta", None), (294, "B", "beta", "r293"),
        (402, "A", "alpha", None), (403, "A", "alpha", "r402"),
        (404, "A", "alpha", "r403"), (405, "B", "beta", None),
        (406, "B", "beta", "r405"), (407, "A", "alpha", None),
        (408, "A", "alpha", "r407"), (409, "A", "alpha", "r408"),
        (410, "A", "alpha", "r409"),
    ]
    for seq, hint, family, parent in rows:
        rid = f"r{seq}"
        _req(db, rid, seq=seq, parent=parent, hint=hint)
        _stream_blocks(db, rid, family)
    dashboard = crud.get_dashboard_live(db)
    session = crud.get_session_conversations(db, "s1")
    assert dashboard["conversation_count"] == session["conversation_count"] == 2
    assert dashboard["conversations"] == session["conversations"]
    assert [group["latest_request_id"] for group in session["conversations"]] == ["r410", "r406"]
    assert dashboard["most_recent_conversation_key"] == session["conversations"][0]["key"]
    assert session["primary_key"] == session["conversations"][0]["key"]
    main, other = session["conversations"]
    assert [card["id"] for segment in main["recent_segments"]
            for card in segment["request_flow"]] == ["r410", "r409", "r408", "r407", "r404", "r403", "r402"]
    assert next(segment for segment in main["recent_segments"]
                if segment["first_request_id"] == "r407")["gap_reason"] == "stream_resume_unlinked"
    assert next(card for segment in main["recent_segments"] for card in segment["request_flow"]
                if card["id"] == "r407")["lineage_relation"] == "context_affinity"
    assert next(card for segment in main["recent_segments"] for card in segment["request_flow"]
                if card["id"] == "r410")["lineage_relation"] == "exact"
    assert other["evidence"] == "stream_affinity"
    assert [card["id"] for segment in other["recent_segments"]
            for card in segment["request_flow"]][:2] == ["r406", "r405"]
    assert next(segment for segment in other["recent_segments"]
                if segment["first_request_id"] == "r405")["gap_reason"] == "stream_resume_unlinked"
    assert next(card for segment in other["recent_segments"] for card in segment["request_flow"]
                if card["id"] == "r405")["lineage_relation"] == "context_affinity"
    assert other["context_change"]["parent_request_id"] == "r405"
    assert main["context_change"]["parent_request_id"] == "r409"
    assert main["next_request_cursor"] is None
    graph, _ = crud._session_lineage_graph(db, "s1", len(rows))
    assert all(edge["target_request_id"] not in {"r405", "r407"} for edge in graph["edges"])


def test_session_projection_confirmed_fork_shared_history_and_external_parent(db):
    _session(db)
    _session(db, "old", active=False)
    _req(db, "external", sid="old", seq=1, tin=20)
    _req(db, "root", seq=1, tin=100, parent="external")
    _req(db, "a", seq=2, parent="root")
    _req(db, "b", seq=3, parent="root")
    _req(db, "aa", seq=4, parent="a")
    _req(db, "bb", seq=5, parent="b")
    result = crud.get_session_conversations(db, "s1")
    assert result["conversation_count"] == 2
    assert {group["key"] for group in result["conversations"]} == {
        group["key"] for group in crud.get_dashboard_live(db)["conversations"]
    }
    assert all(any(card["id"] == "root" and card["shared_history"]
                   for segment in group["recent_segments"] for card in segment["request_flow"])
               for group in result["conversations"])
    graph, _ = crud._session_lineage_graph(db, "s1", 5)
    assert next(node for node in graph["nodes"] if node["request_id"] == "root")["conversation_fork_status"] == "confirmed"
    assert all(card["id"] != "external" for group in result["conversations"]
               for segment in group["recent_segments"] for card in segment["request_flow"])


def test_session_conversations_null_sequence_cursor(db):
    _session(db)
    for index in range(18):
        _req(db, f"n{index}", seq=None, minutes=index)
    result = crud.get_session_conversations(db, "s1")
    group = result["conversations"][0]
    page = crud.get_session_conversation_requests(
        db, "s1", group["key"], revision=result["revision"],
        cursor=group["next_request_cursor"], limit=2,
    )
    assert [card["id"] for segment in page["segments"] for card in segment["request_flow"]] == ["n2", "n1"]


def test_session_conversation_group_pagination_and_cursor_validation(db):
    _session(db)
    _req(db, "root", seq=1)
    for branch in range(5):
        child = f"child-{branch}"
        _req(db, child, seq=branch + 2, parent="root")
        _req(db, f"grandchild-{branch}", seq=branch + 7, parent=child)
    first = crud.get_session_conversations(db, "s1")
    assert first["conversation_count"] == 5
    assert len(first["conversations"]) == 4
    assert first["next_group_offset"] == 4
    second = crud.get_session_conversations(db, "s1", group_offset=4, revision=first["revision"])
    assert len(second["conversations"]) == 1
    assert second["next_group_offset"] is None
    assert len({group["key"] for group in first["conversations"] + second["conversations"]}) == 5
    pinned = crud.get_session_conversations(
        db, "s1", revision=first["revision"], group_key=second["conversations"][0]["key"],
    )
    assert [group["key"] for group in pinned["conversations"]] == [second["conversations"][0]["key"]]
    with pytest.raises(KeyError, match="group not found"):
        crud.get_session_conversation_requests(db, "s1", "not-a-group", revision=first["revision"])
    with pytest.raises(ValueError, match="cursor does not belong"):
        crud.get_session_conversation_requests(
            db, "s1", first["conversations"][0]["key"], revision=first["revision"],
            cursor=crud._cursor_encode((9999, "2026-01-01", "other")),
        )


@pytest.mark.parametrize("size", [500, 2000])
def test_large_session_conversations_are_bounded_and_paged(db, size):
    _session(db, active=False)
    db.add_all(Request(
        id=f"large-{index}", session_id="s1", session_seq=index,
        timestamp=T0 + timedelta(seconds=index), provider="anthropic",
        endpoint="/v1/messages", tokens_total_input=10, tokens_total_output=1,
        context_fidelity="opaque",
    ) for index in range(1, size + 1))
    db.flush()
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = crud.get_session_conversations(db, "s1")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result["conversation_count"] == 1
    assert result["lineage_fragment_count"] == size
    assert result["conversations"][0]["request_count"] == size
    assert sum(len(segment["request_flow"])
               for segment in result["conversations"][0]["recent_segments"]) == 15
    assert len(statements) <= 9


def test_route_exposes_crud_contract(tmp_path):
    database.init_db(tmp_path / "t.db")
    try:
        app = FastAPI()
        app.include_router(stats_router.router, prefix="/api")
        client = TestClient(app)
        resp = client.get("/api/stats/dashboard-live")
        assert resp.status_code == 200
        assert resp.json()["active_session"] is None
        assert resp.json()["conversations"] == []
        with database.get_db() as session:
            _session(session)
            _req(session, "r1", seq=1, tin=5)
        body = client.get("/api/stats/dashboard-live").json()
        assert body["active_session"]["request_count"] == 1
        assert body["context_change"]["request_id"] == "r1"
    finally:
        database.dispose_engine()


def test_session_conversation_routes_return_revision_and_refresh_condition(tmp_path):
    database.init_db(tmp_path / "conversations.db")
    try:
        app = FastAPI()
        app.include_router(sessions_router.router, prefix="/api")
        client = TestClient(app)
        with database.get_db() as session:
            _session(session, active=False)
            for seq in range(1, 18):
                _req(session, f"r{seq}", seq=seq, parent=f"r{seq - 1}" if seq > 1 else None)
        response = client.get("/api/sessions/s1/conversations")
        assert response.status_code == 200
        body = response.json()
        group = body["conversations"][0]
        page = client.get("/api/sessions/s1/conversations/requests", params={
            "group_key": group["key"], "revision": body["revision"],
            "cursor": group["next_request_cursor"],
        })
        assert page.status_code == 200
        assert [card["id"] for segment in page.json()["segments"]
                for card in segment["request_flow"]] == ["r2", "r1"]
        with database.get_db() as session:
            _req(session, "r18", seq=18, parent="r17")
        stale = client.get("/api/sessions/s1/conversations/requests", params={
            "group_key": group["key"], "revision": body["revision"],
        })
        assert stale.status_code == 409
        assert client.get("/api/sessions/missing/conversations").status_code == 404
    finally:
        database.dispose_engine()


def test_session_conversations_pin_one_read_snapshot_during_append(tmp_path, monkeypatch):
    from sqlalchemy import text
    engine = create_engine(f"sqlite:///{tmp_path / 'session-snapshot.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
    with OrmSession(engine) as writer:
        _session(writer, active=False)
        _req(writer, "before", seq=1)
        writer.commit()
    original = crud.get_session_lineage_snapshots
    inserted = False
    def append_between_reads(reader, session_id):
        nonlocal inserted
        if not inserted:
            inserted = True
            with OrmSession(engine) as writer:
                _req(writer, "after", seq=2)
                writer.commit()
        return original(reader, session_id)
    monkeypatch.setattr(crud, "get_session_lineage_snapshots", append_between_reads)
    with OrmSession(engine) as reader:
        first = crud.get_session_conversations(reader, "s1")
    assert first["conversations"][0]["latest_request_id"] == "before"
    assert first["conversations"][0]["request_count"] == 1
    with OrmSession(engine) as reader:
        second = crud.get_session_conversations(reader, "s1")
    assert second["conversations"][0]["latest_request_id"] == "after"
    assert second["revision"] != first["revision"]
