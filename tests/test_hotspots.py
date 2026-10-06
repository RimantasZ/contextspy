"""Hot spots: ranking blocks, sources and files of a scope by the visible tokens they carry (Plan 4b)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.block_hotspots import block_label, count_runs, preview_text, share_pct, unpack_latest
from contextspy.db import block_occurrence_service as occurrences
from contextspy.db import hotspots_service as service
from contextspy.db.models import Base, BlockContent, BlockRecord, Request, Session

T0 = datetime(2026, 10, 1, 8, 0, 0)


# --------------------------------------------------------------------------- pure helpers

def test_count_runs():
    assert count_runs([]) == 0
    assert count_runs([3]) == 1
    assert count_runs([0, 1, 2, 5, 6, 9]) == 3
    assert count_runs([5, 1, 2, 2, 1]) == 2  # unordered and repeated positions are fine


def test_unpack_latest_round_trips_position_and_block_id():
    assert unpack_latest(7 * (1 << 32) + 123456) == (7, 123456)


def test_share_and_preview():
    assert share_pct(1, 3) == 33.3 and share_pct(5, 0) == 0.0
    assert preview_text("  a\n\n  b\tc  ") == "a b c"
    assert preview_text("x" * 500).endswith("…") and len(preview_text("x" * 500)) == 120
    assert preview_text(None) is None and preview_text("   ") is None


@pytest.mark.parametrize("kwargs, expected", [
    (dict(block_type="tool_result", tool_name="Read", source_key="tool:Read", file_path="/a.py", provider_item_type=None),
     "tool:Read result · /a.py"),
    (dict(block_type="tool_call", tool_name="exec", source_key=None, file_path=None, provider_item_type=None), "exec call"),
    (dict(block_type="tool_definition", tool_name="Bash", source_key="tool:Bash", file_path=None, provider_item_type=None),
     "tool:Bash definition"),
    (dict(block_type="other", tool_name=None, source_key="other", file_path=None, provider_item_type="compaction"), "compaction"),
    (dict(block_type="other", tool_name=None, source_key="other", file_path=None, provider_item_type=None), "other"),
    (dict(block_type="system_prompt", tool_name=None, source_key="system", file_path=None, provider_item_type=None), "System prompt"),
    (dict(block_type="thinking", tool_name=None, source_key="reasoning", file_path=None, provider_item_type=None), "Reasoning"),
])
def test_block_label(kwargs, expected):
    block_type = kwargs.pop("block_type")
    assert block_label(block_type, **kwargs) == expected


# --------------------------------------------------------------------------- fixtures

@pytest.fixture
def db():
    occurrences.clear_membership_cache()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with OrmSession(engine) as session:
        yield session
    occurrences.clear_membership_cache()


def _session(db, sid="s1"):
    db.add(Session(id=sid, name=sid, started_at=T0, is_active=1))
    db.flush()


def _req(db, rid, seq, *, sid="s1", parent=None, fidelity="complete"):
    db.add(Request(
        id=rid, session_id=sid, session_seq=seq, timestamp=T0 + timedelta(minutes=seq),
        provider="anthropic", endpoint="/v1/messages", tokens_total_input=1, tokens_total_output=1,
        context_fidelity=fidelity, provider_response_id=rid, predecessor_response_id=parent,
    ))
    db.flush()


def _block(db, rid, content_hash, *, tokens=10, position=0, block_type="tool_result", direction="input",
           source_key=None, category=None, file_path=None, tool_name=None, attrs=None, content=None):
    row = BlockRecord(
        request_id=rid, direction=direction, position=position, block_type=block_type, content_hash=content_hash,
        token_count=tokens, source_key=source_key, category=category, file_path=file_path, tool_name=tool_name,
        attrs=json.dumps(attrs) if attrs else None,
    )
    db.add(row)
    if content is not None:
        db.merge(BlockContent(hash=content_hash, content=content, created_at=T0))
    db.flush()
    return row.id


def _chain(db, count, *, sid="s1"):
    _session(db, sid)
    for index in range(1, count + 1):
        _req(db, f"r{index}", index, sid=sid, parent=f"r{index - 1}" if index > 1 else None)


def _fork(db):
    """root, a, b, aa, bb (session order); C1 = root, a, aa and C2 = root, b, bb. The latest request (bb) is in C2.

    H (10 tokens) is in root, a, aa; G (20) in b, bb; S (5) in root, b, bb; R (7) in root and bb only.
    """
    _session(db)
    for rid, seq, parent in (("root", 1, None), ("a", 2, "root"), ("b", 3, "root"), ("aa", 4, "a"), ("bb", 5, "b")):
        _req(db, rid, seq, parent=parent)
    for rid in ("root", "a", "aa"):
        _block(db, rid, "H", tokens=10, source_key="tool:Read")
    for rid in ("b", "bb"):
        _block(db, rid, "G", tokens=20, source_key="tool:Grep")
    for rid in ("root", "b", "bb"):
        _block(db, rid, "S", tokens=5, position=1, source_key="tool:Bash")
    for rid in ("root", "bb"):
        _block(db, rid, "R", tokens=7, position=2, source_key="tool:Bash")


def _keys(result):
    return [row["key"] for row in result["rows"]]


# --------------------------------------------------------------------------- counting

def test_totals_counts_and_repeats_inside_a_request(db):
    _chain(db, 3)
    for rid in ("r1", "r2", "r3"):
        _block(db, rid, "A", tokens=10)
    _block(db, "r2", "A", tokens=10, position=1)        # repeated in one request: occurrence, not request
    _block(db, "r3", "B", tokens=4, position=1)
    _block(db, "r1", "B", tokens=6, position=1)          # B's occurrences disagree on size
    result = service.get_session_hotspots(db, "s1", scope="session")
    a, b = result["rows"]
    assert (a["key"], a["occurrence_count"], a["request_count"], a["total_tokens"]) == ("A", 4, 3, 40)
    assert a["tokens_per_occurrence"] == 10
    assert (b["key"], b["occurrence_count"], b["tokens_per_occurrence"], b["total_tokens"]) == ("B", 2, None, 10)
    assert result["summary"]["visible_tokens_total"] == 50
    assert result["summary"]["scope_request_count"] == 3 and result["summary"]["fidelity_counts"] == {"complete": 3}
    assert a["share_pct"] == 80.0 and b["share_pct"] == 20.0
    assert result["summary"]["returned_tokens"] == 50 and result["summary"]["returned_share_pct"] == 100.0
    assert (a["first_seen_session_seq"], a["last_seen_session_seq"], a["in_latest_request"]) == (1, 3, True)
    assert (b["first_seen_session_seq"], b["last_seen_session_seq"], b["in_latest_request"]) == (1, 3, True)


def test_sorting_ties_and_paging(db):
    _chain(db, 4)
    # X: 4 occurrences x 1 token, Y: 1 x 9, Z: 2 x 2, W: 2 x 2 (ties with Z)
    for rid in ("r1", "r2", "r3", "r4"):
        _block(db, rid, "X", tokens=1)
    _block(db, "r1", "Y", tokens=9, position=1)
    for rid in ("r1", "r2"):
        _block(db, rid, "Z", tokens=2, position=2)
        _block(db, rid, "W", tokens=2, position=3)
    by_tokens = service.get_session_hotspots(db, "s1", scope="session", sort="total_tokens")
    # 9 first; then X, W and Z all total 4: more occurrences first (X), the rest by key.
    assert _keys(by_tokens) == ["Y", "X", "W", "Z"]
    by_occurrences = service.get_session_hotspots(db, "s1", scope="session", sort="occurrences")
    assert _keys(by_occurrences) == ["X", "W", "Z", "Y"]  # 4, then W/Z (2, equal totals -> key), then 1
    page = service.get_session_hotspots(db, "s1", scope="session", limit=2, offset=1)
    assert _keys(page) == ["X", "W"] and page["total_rows"] == 4 and page["has_more"] is True
    last = service.get_session_hotspots(db, "s1", scope="session", limit=2, offset=3)
    assert _keys(last) == ["Z"] and last["has_more"] is False
    assert last["summary"]["returned_tokens"] == 4


def test_empty_session_and_unknown_session(db):
    _session(db)
    result = service.get_session_hotspots(db, "s1", scope="session")
    assert result["rows"] == [] and result["total_rows"] == 0 and result["summary"]["visible_tokens_total"] == 0
    assert result["summary"]["scope_request_count"] == 0 and result["has_more"] is False
    assert service.get_session_hotspots(db, "nope") is None


# --------------------------------------------------------------------------- scope

def test_default_scope_is_the_conversation_of_the_latest_request(db):
    _fork(db)
    result = service.get_session_hotspots(db, "s1")
    assert (result["scope"], result["scope_note"], result["requested_scope"]) == ("conversation", None, "conversation")
    assert result["summary"]["scope_request_count"] == 3  # root, b, bb
    totals = {row["key"]: row["total_tokens"] for row in result["rows"]}
    assert totals == {"G": 40, "R": 14, "S": 15, "H": 10}  # H only through the shared root
    assert len(result["conversations"]) == 2
    selected = [c for c in result["conversations"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["request_count"] == 3
    assert {c["code"] for c in result["conversations"]} == {"C1", "C2"}


def test_explicit_conversation_session_scope_and_unknown_conversation(db):
    _fork(db)
    keys = {c["key"]: c for c in service.get_session_hotspots(db, "s1")["conversations"]}
    other = next(key for key, c in keys.items() if not c["selected"])
    c1 = service.get_session_hotspots(db, "s1", conversation=other)
    assert {row["key"]: row["total_tokens"] for row in c1["rows"]} == {"H": 30, "S": 5, "R": 7}
    assert [c["key"] for c in c1["conversations"] if c["selected"]] == [other]

    whole = service.get_session_hotspots(db, "s1", scope="session")
    assert whole["scope"] == "session" and whole["summary"]["scope_request_count"] == 5
    assert {row["key"]: row["total_tokens"] for row in whole["rows"]} == {"H": 30, "G": 40, "S": 15, "R": 14}
    assert len(whole["conversations"]) == 2 and not any(c["selected"] for c in whole["conversations"])

    missing = service.get_session_hotspots(db, "s1", conversation="no-such-key")
    assert (missing["scope"], missing["scope_note"]) == ("session", "conversation_unavailable")


def test_the_scope_matches_the_one_used_by_the_block_panel(db):
    _fork(db)
    request = db.get(Request, "bb")
    legacy = occurrences._scope_requests(db, request, "conversation")[0]
    resolved = occurrences.scope_for_session(db, "s1", "conversation", anchor_request_id="bb")
    assert [r.request_id for r in resolved.requests] == [r.request_id for r in legacy]
    assert [r.request_id for r in service.get_session_hotspots(db, "s1")["rows"] and resolved.requests] == ["root", "b", "bb"]


# --------------------------------------------------------------------------- in context, runs

def test_in_context_filter_and_run_count(db):
    _fork(db)
    everything = service.get_session_hotspots(db, "s1")
    rows = {row["key"]: row for row in everything["rows"]}
    assert rows["H"]["in_latest_request"] is False and rows["G"]["in_latest_request"] is True
    assert rows["R"]["run_count"] == 2 and rows["S"]["run_count"] == 1  # R: root and bb, with b between
    current = service.get_session_hotspots(db, "s1", in_context="current")
    dropped = service.get_session_hotspots(db, "s1", in_context="dropped")
    assert set(_keys(current)) == {"G", "R", "S"} and _keys(dropped) == ["H"]
    assert current["total_rows"] == 3 and dropped["total_rows"] == 1
    # The summary still describes the whole scope.
    assert dropped["summary"]["visible_tokens_total"] == everything["summary"]["visible_tokens_total"]


def test_a_block_that_goes_away_and_returns_has_several_runs(db):
    _chain(db, 5)
    for rid in ("r1", "r2", "r4", "r5"):
        _block(db, rid, "A")
    row = service.get_session_hotspots(db, "s1", scope="session")["rows"][0]
    assert (row["run_count"], row["occurrence_count"], row["in_latest_request"]) == (2, 4, True)


# --------------------------------------------------------------------------- filters and exclusions

def test_filters_unidentifiable_blocks_and_output_blocks(db):
    _chain(db, 2)
    _block(db, "r1", "A", tokens=10, category="file_contents", block_type="tool_result", source_key="tool:Read")
    _block(db, "r2", "A", tokens=10, category="file_contents", block_type="tool_result", source_key="tool:Read")
    _block(db, "r1", "B", tokens=3, position=1, category="system_prompt", block_type="system_prompt", source_key="system")
    _block(db, "r1", None, tokens=4, position=2, block_type="thinking")        # hidden: no identity
    _block(db, "r2", None, tokens=6, position=2, block_type="thinking")
    _block(db, "r1", "OUT", tokens=99, direction="output", block_type="assistant_message")
    result = service.get_session_hotspots(db, "s1", scope="session")
    assert _keys(result) == ["A", "B"]  # hash-less and output blocks are not rows
    assert result["summary"]["unidentifiable"] == {"blocks": 2, "tokens": 10}
    assert result["summary"]["visible_tokens_total"] == 33  # 20 + 3 + 10; the output block is never counted
    only_files = service.get_session_hotspots(db, "s1", scope="session", category="file_contents")
    assert _keys(only_files) == ["A"] and only_files["summary"]["visible_tokens_total"] == 20
    assert _keys(service.get_session_hotspots(db, "s1", scope="session", block_type="system_prompt")) == ["B"]
    assert _keys(service.get_session_hotspots(db, "s1", scope="session", source="tool:Read")) == ["A"]


def test_row_describes_the_latest_occurrence_and_lists_other_block_types(db):
    _chain(db, 3)
    first = _block(db, "r1", "A", tokens=10, block_type="tool_call", tool_name="Read", source_key="tool:Read")
    _block(db, "r2", "A", tokens=10, block_type="tool_result", tool_name="Read", source_key="tool:Read",
           file_path="/x/y.py", content="def main():\n    return 1\n")
    last = _block(db, "r3", "A", tokens=10, block_type="tool_result", tool_name="Read", source_key="tool:Read",
                  file_path="/x/y.py")
    row = service.get_session_hotspots(db, "s1", scope="session")["rows"][0]
    assert row["block_type"] == "tool_result" and row["block_types"] == ["tool_call", "tool_result"]
    assert row["latest"] == {"request_id": "r3", "block_id": last, "session_seq": 3}
    assert row["latest"]["block_id"] != first
    assert row["label"] == "tool:Read result · /x/y.py" and row["activity"] == "read" and row["file_path"] == "/x/y.py"
    assert row["preview"] == "def main(): return 1" and row["content_purged"] is False


def test_labels_for_compaction_items_need_no_content(db):
    _chain(db, 1)
    _block(db, "r1", "C", tokens=500, block_type="other", source_key="other", attrs={"provider_item_type": "compaction"})
    row = service.get_session_hotspots(db, "s1", scope="session")["rows"][0]
    assert row["label"] == "compaction" and row["preview"] is None and row["content_purged"] is True


# --------------------------------------------------------------------------- source and file grouping

def test_source_grouping(db):
    _fork(db)
    _block(db, "bb", None, tokens=1, position=5, block_type="thinking")           # no source key -> "unknown"
    result = service.get_session_hotspots(db, "s1", group="source", scope="session")
    rows = {row["source_key"]: row for row in result["rows"]}
    assert rows["tool:Grep"]["total_tokens"] == 40 and rows["tool:Grep"]["occurrence_count"] == 2
    assert rows["tool:Bash"]["distinct_blocks"] == 2 and rows["tool:Bash"]["request_count"] == 3
    assert rows["unknown"]["total_tokens"] == 1 and rows["unknown"]["distinct_blocks"] == 0
    assert rows["tool:Grep"]["activity"] == "search" and rows["tool:Grep"]["share_pct"] == round(100 * 40 / 100, 1)
    largest = rows["tool:Grep"]["largest"]
    assert (largest["token_count"], largest["request_id"]) == (20, "bb") and largest["session_seq"] == 5
    assert result["summary"]["unidentifiable"] is None
    assert result["rows"][0]["source_key"] == "tool:Grep"


def test_file_grouping_separates_reads_from_edits(db):
    _chain(db, 3)
    for rid in ("r1", "r2", "r3"):
        _block(db, rid, "call-1" if rid == "r1" else "call-2", tokens=30 if rid == "r1" else 5, block_type="tool_call",
               file_path="/p/a.py", source_key="tool:Write")
    for rid in ("r2", "r3"):
        _block(db, rid, "read-1", tokens=100, position=1, block_type="tool_result", file_path="/p/a.py")
    _block(db, "r3", "read-2", tokens=40, position=2, block_type="tool_result", file_path="/p/b.py")
    _block(db, "r3", "plain", tokens=60, position=3)                               # no file: counts toward the total only
    result = service.get_session_hotspots(db, "s1", group="file", scope="session")
    a, b = result["rows"]
    assert a["file_path"] == "/p/a.py" and a["result_tokens"] == 200 and a["call_tokens"] == 40
    assert a["total_tokens"] == a["result_tokens"] + a["call_tokens"] == 240
    assert (a["distinct_versions"], a["occurrence_count"], a["request_count"]) == (3, 5, 3)
    assert (a["first_seen_session_seq"], a["last_seen_session_seq"], a["in_latest_request"]) == (1, 3, True)
    assert b["file_path"] == "/p/b.py" and b["total_tokens"] == 40 and b["call_tokens"] == 0
    assert result["summary"]["visible_tokens_total"] == 240 + 40 + 60 + 5 * 0  # shares are of the whole scope
    assert a["share_pct"] == round(100 * 240 / 340, 1)
    assert a["latest"]["session_seq"] == 3


# --------------------------------------------------------------------------- archived data and plan shape

def test_numbers_do_not_depend_on_stored_content(db):
    _chain(db, 2)
    for rid in ("r1", "r2"):
        _block(db, rid, "A", tokens=10, content="hello world")
    before = service.get_session_hotspots(db, "s1", scope="session")["rows"][0]
    db.query(BlockContent).delete()
    after = service.get_session_hotspots(db, "s1", scope="session")["rows"][0]
    assert before["preview"] == "hello world" and before["content_purged"] is False
    assert after["preview"] is None and after["content_purged"] is True
    ignore = {"preview", "content_purged"}
    assert {k: v for k, v in before.items() if k not in ignore} == {k: v for k, v in after.items() if k not in ignore}


def test_aggregation_drives_from_the_scope_table_and_not_the_content_hash_index(db):
    _chain(db, 30)
    for index in range(1, 31):
        for position in range(40):
            _block(db, f"r{index}", f"h{position % 25}-{index % 3}", tokens=7, position=position,
                   source_key="tool:Read", file_path="/f.py" if position == 0 else None)
    db.execute(text("ANALYZE"))
    scope = occurrences.scope_for_session(db, "s1", "session")
    for group in service.GROUPS:
        service._load_scope(db, scope)
        sql, params = service.aggregate_select(group, category="x", block_type="tool_result", source="tool:Read")
        plan = [row[3] for row in db.execute(text(f"EXPLAIN QUERY PLAN {sql}"), params)]
        service._drop_temp(db)
        assert plan[0].startswith("SCAN s"), plan
        assert any("SEARCH b USING INDEX idx_blocks_request" in line for line in plan), plan
        assert not any("idx_blocks_content_hash" in line or line.startswith("SCAN b") for line in plan), plan


def test_statement_count_is_constant_as_requests_grow(db):
    _chain(db, 60)
    for index in range(1, 61):
        _block(db, f"r{index}", "H", tokens=5)
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))

    def count(limit_requests):
        occurrences.clear_membership_cache()
        statements.clear()
        service.get_session_hotspots(db, "s1", scope="session")
        return len(statements)

    first = count(1)
    for index in range(61, 121):
        _req(db, f"r{index}", index, parent=f"r{index - 1}")
        _block(db, f"r{index}", "H", tokens=5)
    assert count(2) == first and first <= 25


def test_temp_tables_are_cleaned_up(db):
    _fork(db)
    service.get_session_hotspots(db, "s1")
    names = {row[0] for row in db.execute(text("SELECT name FROM sqlite_temp_master"))}
    assert not names & {"hs_scope", "hs_agg"}


# --------------------------------------------------------------------------- HTTP

@pytest.fixture
def client(tmp_path):
    from contextspy.api.routers import sessions as sessions_router
    from contextspy.db.database import get_db, init_db

    occurrences.clear_membership_cache()
    init_db(tmp_path / "hotspots.db")
    with get_db() as session:
        _chain(session, 3)
        for rid in ("r1", "r2", "r3"):
            _block(session, rid, "A", tokens=10, source_key="tool:Read")
    app = FastAPI()
    app.include_router(sessions_router.router, prefix="/api")
    yield TestClient(app)
    occurrences.clear_membership_cache()


def test_endpoint_returns_the_ranking(client):
    body = client.get("/api/sessions/s1/hotspots").json()
    assert body["group"] == "block" and body["scope"] == "conversation"
    assert body["rows"][0]["total_tokens"] == 30 and body["summary"]["visible_tokens_total"] == 30
    sources = client.get("/api/sessions/s1/hotspots", params={"group": "source", "scope": "session"}).json()
    assert sources["rows"][0]["source_key"] == "tool:Read"


@pytest.mark.parametrize("params", [
    {"group": "tree"}, {"scope": "all"}, {"sort": "size"}, {"in_context": "maybe"}, {"limit": 0}, {"limit": 101},
    {"offset": -1}, {"offset": 1001}, {"group": "source", "in_context": "current"},
])
def test_endpoint_rejects_bad_parameters(client, params):
    assert client.get("/api/sessions/s1/hotspots", params=params).status_code == 422


def test_endpoint_404_for_an_unknown_session(client):
    assert client.get("/api/sessions/nope/hotspots").status_code == 404
