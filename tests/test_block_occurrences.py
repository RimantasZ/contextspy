"""Block occurrences: pure run/total aggregation, the DB scope logic and the HTTP endpoints."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.block_occurrences import OccurrenceIndex, OccurrenceRow, SAMPLE_LIMIT, ScopeRequest
from contextspy.db import block_occurrence_service as service
from contextspy.db import crud
from contextspy.db.models import Base, BlockRecord, Request, Session

T0 = datetime(2026, 10, 1, 8, 0, 0)


# --------------------------------------------------------------------------- pure aggregation

def _scope(seqs, fidelity="complete", code="C1"):
    return [ScopeRequest(f"r{seq}", seq, code, fidelity) for seq in seqs]


def _rows(*pairs):
    """(seq, block_id, tokens) -> rows."""
    return [OccurrenceRow(f"r{seq}", block_id, tokens) for seq, block_id, tokens in pairs]


def _index(scope, rows, current=("r1", 1)):
    return OccurrenceIndex(scope, rows, current_request_id=current[0], current_block_id=current[1])


def test_runs_follow_positions_in_the_scope_not_session_sequence_numbers():
    # Conversation C1 owns seq 12, 13, 15, 16 (14 belongs to another conversation). The block is in all four.
    index = _index(_scope([12, 13, 15, 16]), _rows((12, 1, 5), (13, 2, 5), (15, 3, 5), (16, 4, 5)), ("r12", 1))
    assert index.runs() == [{
        "from_position": 0, "to_position": 3, "from_seq": 12, "to_seq": 16, "request_count": 4, "occurrence_count": 4,
    }]
    # The same block seen through the whole session is split where seq 14 does not contain it.
    session = [ScopeRequest(f"r{s}", s, None, "complete") for s in (12, 13, 14, 15, 16)]
    runs = _index(session, _rows((12, 1, 5), (13, 2, 5), (15, 3, 5), (16, 4, 5)), ("r12", 1)).runs()
    assert [(r["from_position"], r["to_position"]) for r in runs] == [(0, 1), (3, 4)]


def test_a_real_gap_makes_two_runs_and_repeats_inside_a_request_count_as_occurrences():
    rows = _rows((1, 1, 10), (2, 2, 10), (2, 3, 10), (5, 4, 10))
    index = _index(_scope([1, 2, 3, 4, 5]), rows)
    assert index.runs() == [
        {"from_position": 0, "to_position": 1, "from_seq": 1, "to_seq": 2, "request_count": 2, "occurrence_count": 3},
        {"from_position": 4, "to_position": 4, "from_seq": 5, "to_seq": 5, "request_count": 1, "occurrence_count": 1},
    ]
    totals = index.totals()
    assert (totals["occurrence_count"], totals["request_count"], totals["total_visible_tokens"]) == (4, 3, 40)
    assert totals["tokens_per_occurrence"] == 10
    assert totals["in_latest_request_of_scope"] is True
    assert (totals["first_seen_session_seq"], totals["last_seen_session_seq"], totals["scope_request_count"]) == (1, 5, 5)


def test_latest_flag_and_differing_token_counts():
    index = _index(_scope([1, 2, 3]), _rows((1, 1, 10), (2, 2, 12)))
    totals = index.totals()
    assert totals["in_latest_request_of_scope"] is False
    assert totals["tokens_per_occurrence"] is None  # occurrences disagree: no single figure
    assert totals["total_visible_tokens"] == 22


def test_rows_outside_the_scope_are_ignored_and_fidelity_is_counted():
    scope = [ScopeRequest("r1", 1, "C1", "complete"), ScopeRequest("r2", 2, "C1", "opaque")]
    rows = _rows((1, 1, 5), (2, 2, 5), (9, 3, 5))  # r9 is not in the scope
    totals = _index(scope, rows).totals()
    assert totals["request_count"] == 2 and totals["fidelity_counts"] == {"complete": 1, "opaque": 1}


def test_empty_presence_is_well_defined():
    totals = _index(_scope([1, 2]), []).totals()
    assert totals["occurrence_count"] == 0 and totals["first_seen_session_seq"] is None
    assert totals["in_latest_request_of_scope"] is False
    assert _index(_scope([1, 2]), []).runs() == [] and _index(_scope([1, 2]), []).sample() == []


def test_sample_contains_first_last_current_and_run_ends_with_the_current_block_id():
    scope = _scope(range(1, 31))
    rows = _rows(*[(s, 100 + s, 5) for s in range(1, 31) if s not in (10, 11, 20)])
    rows += [OccurrenceRow("r15", 7, 5)]  # a second row in r15; the current block there is id 7
    index = _index(scope, rows, ("r15", 7))
    sample = index.sample()
    positions = [e["position"] for e in sample]
    assert positions == sorted(positions)
    assert {0, 29, 14, 8, 11, 17, 18, 20}.issubset(set(positions)) or {0, 29, 14}.issubset(set(positions))
    current = next(e for e in sample if e["is_current"])
    assert (current["request_id"], current["block_id"]) == ("r15", 7)
    other = next(e for e in sample if e["request_id"] == "r1")
    assert other["block_id"] == 101  # the occurrence's own block in that request


def test_sample_is_capped_and_expansion_pages_through_a_run():
    scope = _scope(range(1, 400))
    present = [s for s in range(1, 400) if s % 2]  # many runs of length one
    index = _index(scope, _rows(*[(s, s, 1) for s in present]))
    assert len(index.sample()) <= SAMPLE_LIMIT
    entries, more = index.entries(0, 398, 100)
    assert len(entries) == 100 and more is True
    entries, more = index.entries(0, 10, 100)
    assert [e["session_seq"] for e in entries] == [1, 3, 5, 7, 9, 11] and more is False


# --------------------------------------------------------------------------- DB scope logic

@pytest.fixture
def db():
    service.clear_membership_cache()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with OrmSession(engine) as session:
        yield session
    service.clear_membership_cache()


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
           tool_call_id=None, source_key=None):
    row = BlockRecord(
        request_id=rid, direction=direction, position=position, block_type=block_type,
        content_hash=content_hash, token_count=tokens, tool_call_id=tool_call_id, source_key=source_key,
    )
    db.add(row)
    db.flush()
    return row.id


def _fork(db):
    """Two conversations that share their root; session order root, a, b, aa, bb.

    C1 = root, a, aa and C2 = root, b, bb, so the other conversation's request sits between C1's.
    H is in every C1 request, G in every C2 request, S in root and every C2 request.
    """
    _session(db)
    for rid, seq, parent in (("root", 1, None), ("a", 2, "root"), ("b", 3, "root"), ("aa", 4, "a"), ("bb", 5, "b")):
        _req(db, rid, seq, parent=parent)
    ids = {f"H-{rid}": _block(db, rid, "H", source_key="tool:Read") for rid in ("root", "a", "aa")}
    ids.update({f"G-{rid}": _block(db, rid, "G") for rid in ("b", "bb")})
    ids.update({f"S-{rid}": _block(db, rid, "S", position=1) for rid in ("root", "b", "bb")})
    return ids


def test_conversation_scope_is_one_run_while_session_scope_is_split_by_the_other_conversation(db):
    ids = _fork(db)

    conversation = service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert conversation["scope"] == "conversation" and conversation["scope_note"] is None
    # root(1), a(2), aa(4): b(3) belongs to the other conversation, so there is no gap here.
    assert [(r["from_seq"], r["to_seq"], r["request_count"]) for r in conversation["ranges"]] == [(1, 4, 3)]
    assert conversation["totals"]["scope_request_count"] == 3
    # Each entry carries the code of the conversation that request is filed under (as on its dashboard card).
    # root is shared history and may be filed under either conversation; a and aa are in the same one.
    codes = {e["request_id"]: e["conversation_code"] for e in conversation["requests_sample"]}
    assert codes["a"] == codes["aa"] and codes["a"] in {"C1", "C2"} and codes["root"] in {"C1", "C2"}
    assert conversation["totals"]["in_latest_request_of_scope"] is True
    assert conversation["identity"] == {
        "kind": "content_hash", "block_type": "tool_result", "tool_name": None,
        "source_key": "tool:Read", "activity": "read",
    }

    session = service.get_block_occurrences(db, "a", ids["H-a"], "session")
    assert session["scope"] == "session"
    assert [(r["from_seq"], r["to_seq"]) for r in session["ranges"]] == [(1, 2), (4, 4)]
    assert session["totals"]["scope_request_count"] == 5 and session["totals"]["request_count"] == 3
    assert session["totals"]["total_visible_tokens"] == 30


def test_each_entry_points_at_the_blocks_row_in_its_own_request(db):
    ids = _fork(db)
    result = service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    by_request = {e["request_id"]: e["block_id"] for e in result["requests_sample"]}
    assert by_request == {"root": ids["H-root"], "a": ids["H-a"], "aa": ids["H-aa"]}
    assert [e["is_current"] for e in result["requests_sample"]] == [False, True, False]


def test_the_other_conversation_is_found_through_its_own_scope(db):
    ids = _fork(db)
    result = service.get_block_occurrences(db, "b", ids["G-b"], "conversation")
    assert result["totals"]["scope_request_count"] == 3  # root, b, bb
    assert [(r["from_seq"], r["to_seq"]) for r in result["ranges"]] == [(3, 5)]
    session = service.get_block_occurrences(db, "b", ids["G-b"], "session")
    assert [(r["from_seq"], r["to_seq"]) for r in session["ranges"]] == [(3, 3), (5, 5)]


def test_shared_history_requests_belong_to_both_conversations(db):
    ids = _fork(db)
    # S is in root (shared) and in C2. Seen from C2 all three occurrences are in scope...
    from_c2 = service.get_block_occurrences(db, "b", ids["S-b"], "conversation")
    assert from_c2["totals"]["request_count"] == 3
    # ...and root is filed under one conversation yet is still a member of C1's scope.
    from_root = service.get_block_occurrences(db, "root", ids["S-root"], "conversation")
    assert from_root["totals"]["scope_request_count"] == 3
    from_a = service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert "root" in {e["request_id"] for e in from_a["requests_sample"]}


def test_a_real_auxiliary_request_falls_back_to_the_session_with_a_note(db):
    _session(db)
    for rid, seq, parent in (("a", 1, None), ("x", 2, None), ("b", 3, "a"), ("y", 4, "x"), ("c", 5, "b")):
        _req(db, rid, seq, parent=parent)  # a-b-c is the conversation; x-y is a short side chain
    aux_block = _block(db, "x", "H")
    _block(db, "y", "H")  # not shared with the conversation: that could create an inferred link
    result = service.get_block_occurrences(db, "x", aux_block, "conversation")
    assert (result["scope"], result["requested_scope"], result["scope_note"]) == ("session", "conversation", "auxiliary_request")
    assert result["totals"]["scope_request_count"] == 5 and result["totals"]["request_count"] == 2
    assert [(r["from_seq"], r["to_seq"]) for r in result["ranges"]] == [(2, 2), (4, 4)]


def test_hashless_blocks_never_merge_across_requests_even_with_the_same_call_id(db):
    _session(db)
    _req(db, "a", 1)
    _req(db, "b", 2, parent="a")
    first = _block(db, "a", None, tool_call_id="call_0")
    _block(db, "b", None, tool_call_id="call_0")
    result = service.get_block_occurrences(db, "a", first, "session")
    assert result["identity"]["kind"] == "none"
    assert result["totals"]["occurrence_count"] == 1 and result["totals"]["request_count"] == 1
    assert [e["request_id"] for e in result["requests_sample"]] == ["a"]


def test_output_blocks_have_no_cross_request_identity(db):
    _session(db)
    _req(db, "a", 1)
    _req(db, "b", 2, parent="a")
    out = _block(db, "a", "same", direction="output", block_type="assistant_message")
    _block(db, "b", "same", direction="output", block_type="assistant_message")
    result = service.get_block_occurrences(db, "a", out, "session")
    assert result["identity"]["kind"] == "none" and result["totals"]["request_count"] == 1


def test_the_same_hash_in_another_session_is_ignored(db):
    _session(db)
    _session(db, "other")
    _req(db, "a", 1)
    _req(db, "o", 1, sid="other")
    mine = _block(db, "a", "H")
    _block(db, "o", "H")
    result = service.get_block_occurrences(db, "a", mine, "session")
    assert result["totals"]["request_count"] == 1 and result["totals"]["scope_request_count"] == 1


def test_requests_without_a_session_see_only_themselves(db):
    db.add(Request(id="solo", session_id=None, session_seq=None, timestamp=T0, provider="p", endpoint="/e",
                   context_fidelity="partial"))
    db.add(Request(id="other", session_id=None, session_seq=None, timestamp=T0, provider="p", endpoint="/e"))
    db.flush()
    mine = _block(db, "solo", "H")
    _block(db, "solo", "H", position=1)  # repeated inside the request
    _block(db, "other", "H")
    result = service.get_block_occurrences(db, "solo", mine, "conversation")
    assert (result["scope"], result["requested_scope"], result["scope_note"]) == ("request", "conversation", "no_session")
    assert result["totals"]["occurrence_count"] == 2 and result["totals"]["request_count"] == 1
    assert result["totals"]["fidelity_counts"] == {"partial": 1}


def test_a_request_filed_in_no_group_falls_back_with_a_note(db, monkeypatch):
    ids = _fork(db)
    real = service._membership(db, "s1", 5)
    monkeypatch.setattr(service, "_membership", lambda *a: real._replace(filed_under={**real.filed_under, "a": None}))
    result = service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert (result["scope"], result["scope_note"]) == ("session", "conversation_unavailable")


def test_unknown_or_foreign_blocks_return_none(db):
    ids = _fork(db)
    assert service.get_block_occurrences(db, "nope", ids["H-a"], "session") is None
    assert service.get_block_occurrences(db, "a", 99999, "session") is None
    assert service.get_block_occurrences(db, "b", ids["H-a"], "session") is None  # block belongs to request a


def test_purged_content_is_irrelevant_because_identity_is_the_stored_hash(db):
    ids = _fork(db)  # no block_contents rows exist at all
    assert service.get_block_occurrences(db, "a", ids["H-a"], "session")["totals"]["request_count"] == 3


def test_membership_is_cached_until_the_request_count_changes(db, monkeypatch):
    ids = _fork(db)
    calls = []
    real = crud.get_session_lineage_graph
    monkeypatch.setattr(crud, "get_session_lineage_graph", lambda *a: calls.append(1) or real(*a))
    service.clear_membership_cache()
    for _ in range(3):
        service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert len(calls) == 1
    service.get_block_occurrences(db, "a", ids["H-a"], "session")  # session scope never needs the graph
    assert len(calls) == 1
    _req(db, "aaa", 6, parent="aa")
    service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert len(calls) == 2  # a new request invalidates immediately


def test_membership_expires_after_the_ttl(db, monkeypatch):
    ids = _fork(db)
    calls = []
    real = crud.get_session_lineage_graph
    monkeypatch.setattr(crud, "get_session_lineage_graph", lambda *a: calls.append(1) or real(*a))
    clock = [1000.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    clock[0] += service.MEMBERSHIP_TTL_SECONDS - 1
    service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert len(calls) == 1
    clock[0] += 2
    service.get_block_occurrences(db, "a", ids["H-a"], "conversation")
    assert len(calls) == 2


def test_session_scope_query_count_does_not_grow_with_the_number_of_requests(db):
    _session(db)
    for index in range(1, 41):
        _req(db, f"r{index}", index, parent=f"r{index - 1}" if index > 1 else None)
    block_ids = {f"r{i}": _block(db, f"r{i}", "H") for i in range(1, 41)}
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))

    def count(request_id):
        statements.clear()
        service.get_block_occurrences(db, request_id, block_ids[request_id], "session")
        return len(statements)

    assert count("r1") == count("r40")
    assert count("r40") <= 5


def test_expansion_lists_the_requests_of_a_run(db):
    ids = _fork(db)
    page = service.list_block_occurrence_requests(db, "a", ids["H-a"], "conversation", 0, 2, 2)
    assert [e["session_seq"] for e in page["requests"]] == [1, 2] and page["has_more"] is True
    assert service.list_block_occurrence_requests(db, "zzz", 1, "session", 0, 1, 10) is None


# --------------------------------------------------------------------------- HTTP

@pytest.fixture
def client(tmp_path):
    from contextspy.api.routers import requests as requests_router
    from contextspy.db.database import get_db, init_db

    service.clear_membership_cache()
    init_db(tmp_path / "occurrences.db")
    with get_db() as session:
        _session(session)
        for rid, seq, parent in (("a", 1, None), ("b", 2, "a"), ("c", 3, "b")):
            _req(session, rid, seq, parent=parent)
        ids = {rid: _block(session, rid, "H") for rid in ("a", "b", "c")}
    app = FastAPI()
    app.include_router(requests_router.router, prefix="/api")
    test_client = TestClient(app)
    test_client.block_ids = ids
    yield test_client
    service.clear_membership_cache()


def test_occurrences_endpoint_returns_the_summary(client):
    body = client.get(f"/api/requests/b/blocks/{client.block_ids['b']}/occurrences").json()
    assert body["scope"] == "conversation" and body["totals"]["request_count"] == 3
    assert [r["from_seq"] for r in body["ranges"]] == [1]
    session = client.get(f"/api/requests/b/blocks/{client.block_ids['b']}/occurrences", params={"scope": "session"}).json()
    assert session["scope"] == "session"


def test_occurrences_endpoint_validates_and_404s(client):
    block = client.block_ids["a"]
    assert client.get(f"/api/requests/a/blocks/{block}/occurrences", params={"scope": "bogus"}).status_code == 422
    assert client.get("/api/requests/missing/blocks/1/occurrences").status_code == 404
    assert client.get(f"/api/requests/b/blocks/{block}/occurrences").status_code == 404  # block of another request
    assert client.get(f"/api/requests/a/blocks/{block}/occurrences/requests").status_code == 422  # to_position required


def test_expansion_endpoint(client):
    block = client.block_ids["a"]
    body = client.get(f"/api/requests/a/blocks/{block}/occurrences/requests",
                      params={"to_position": 5, "limit": 1}).json()
    assert [e["request_id"] for e in body["requests"]] == ["a"] and body["has_more"] is True
