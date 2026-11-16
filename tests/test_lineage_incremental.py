"""Appends update the lineage revision and graph incrementally, with exactly the full result."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.lineage import LineageBuildMemo, build_lineage_graph
from contextspy.db import crud, session_lineage_service as service
from contextspy.db.models import Base, BlockRecord, Request, Session

T0 = datetime(2026, 11, 16, 8, 0, 0)
SID = "s1"
_FULL_EVIDENCE = service._full_evidence  # the reference below must not count as a run


@pytest.fixture(autouse=True)
def _clean_state():
    service.invalidate()
    service._evidence.clear()
    service._cache.clear()
    service._cache_bytes = 0
    yield
    service.invalidate()
    service._evidence.clear()
    service._cache.clear()
    service._cache_bytes = 0


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'incremental.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    service.watch_engine(engine)
    with OrmSession(engine) as db:
        db.add(Session(id=SID, name=SID, started_at=T0, is_active=1))
        db.add(Session(id="other", name="other", started_at=T0, is_active=0))
        db.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def full_runs(monkeypatch):
    """Session ids of every full (not incremental) evidence computation."""
    calls = []
    real = service._full_evidence

    def counting(db, session_id):
        calls.append(session_id)
        return real(db, session_id)

    monkeypatch.setattr(service, "_full_evidence", counting)
    return calls


@pytest.fixture
def loaded(monkeypatch):
    """Request ids whose block rows the snapshot loader reads."""
    calls = []
    real = crud._lineage_snapshots_for_requests

    def recording(db, requests):
        calls.append([request.id for request in requests])
        return real(db, requests)

    monkeypatch.setattr(crud, "_lineage_snapshots_for_requests", recording)
    return calls


class Conversation:
    """Requests shaped like an agent session: each turn resends the transcript so far."""

    def __init__(self, engine):
        self.engine = engine
        self.seq = 0
        self.transcripts: dict[str, list[str]] = {}

    def turn(self, rid, *, parent=None, exact=False, session=SID, new=("user",)):
        """A request continuing ``parent`` (by content, or by provider id if ``exact``)."""
        self.seq += 1
        history = list(self.transcripts.get(parent, [])) + [f"{parent}-out"] if parent else []
        transcript = history + [f"{rid}-{item}" for item in new]
        self.transcripts[rid] = transcript
        with OrmSession(self.engine) as db:
            db.add(Request(
                id=rid, session_id=session, session_seq=self.seq,
                timestamp=T0 + timedelta(seconds=10 * self.seq), provider="anthropic",
                endpoint="/v1/messages", provider_response_id=f"resp-{rid}",
                predecessor_response_id=f"resp-{parent}" if exact and parent else None,
                tokens_total_input=10 * len(transcript), tokens_total_output=5,
                context_fidelity="complete"))
            db.add(BlockRecord(request_id=rid, direction="input", position=0,
                               block_type="system_prompt", category="system_prompt",
                               content_hash="system", token_count=50))
            for position, content in enumerate(transcript, start=1):
                db.add(BlockRecord(
                    request_id=rid, direction="input", position=position,
                    block_type="assistant_message" if content.endswith("-out") else "user_message",
                    category="conversation_history", content_hash=content, token_count=20))
            db.add(BlockRecord(request_id=rid, direction="output", position=0,
                               block_type="assistant_message", content_hash=f"{rid}-out",
                               token_count=5))
            db.commit()


def _count(db, sid=SID):
    return db.scalar(text("select count(*) from requests where session_id = :s"), {"s": sid})


def _revision(engine, sid=SID):
    with OrmSession(engine) as db:
        return crud.get_session_lineage_revision(db, sid)


def _exact(engine, sid=SID):
    """``evidence_revision``, computed from scratch outside the counted path."""
    with OrmSession(engine) as db:
        return service._revision(db, sid, _count(db, sid), _FULL_EVIDENCE(db, sid))


def _graph(engine, sid=SID):
    with OrmSession(engine) as db:
        return crud.get_session_lineage_graph(db, sid)


def _fresh_graph(engine, sid=SID):
    with OrmSession(engine) as db:
        snapshots, external = crud.get_session_lineage_snapshots(db, sid)
    return build_lineage_graph(snapshots, external_requests=external)


def _same(left, right):
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)


def _write(engine, sql, **params):
    with OrmSession(engine) as db:
        db.execute(text(sql), params)
        db.commit()


def test_an_append_updates_the_revision_without_a_full_computation(engine, full_runs):
    chat = Conversation(engine)
    chat.turn("r1")
    chat.turn("r2", parent="r1", exact=True)
    first = _revision(engine)
    assert full_runs == [SID]
    for rid, parent in (("r3", "r2"), ("r4", "r3"), ("r5", "r2")):
        chat.turn(rid, parent=parent, exact=rid != "r5")
        revision = _revision(engine)
        assert revision != first and revision == _exact(engine)
        first = revision
    assert full_runs == [SID]


def test_blocks_appended_to_an_existing_request_change_its_digest(engine, full_runs):
    chat = Conversation(engine)
    chat.turn("r1")
    chat.turn("r2", parent="r1")
    before = _revision(engine)
    with OrmSession(engine) as db:
        db.add(BlockRecord(request_id="r1", direction="output", position=1,
                           block_type="assistant_message", content_hash="late", token_count=1))
        db.commit()
    after = _revision(engine)
    assert after != before and after == _exact(engine)
    assert full_runs == [SID]


def test_an_append_to_another_session_keeps_the_revision(engine, full_runs):
    chat = Conversation(engine)
    chat.turn("r1")
    before = _revision(engine)
    chat.turn("x1", session="other")
    assert _revision(engine) == before == _exact(engine)
    assert full_runs == [SID]


@pytest.mark.parametrize("sql", [
    "update requests set tokens_total_input = 999 where id = 'r1'",
    "update blocks set content_hash = 'edited' where request_id = 'r1'",
    "update requests set session_id = 'other' where id = 'r2'",
    "delete from blocks where request_id = 'r2'",
    "delete from requests where id = 'r2'",
    "insert into blocks (id, request_id, direction, position, block_type, token_count) "
    "values (100000, 'r1', 'input', 9, 'user_message', 1)",
    "insert or replace into blocks (request_id, direction, position, block_type, token_count) "
    "values ('r1', 'input', 9, 'user_message', 1)",
])
def test_any_other_write_recomputes_in_full(engine, full_runs, sql):
    chat = Conversation(engine)
    chat.turn("r1")
    chat.turn("r2", parent="r1")
    before = _revision(engine)
    _write(engine, sql)
    after = _revision(engine)
    assert after != before and after == _exact(engine)
    assert full_runs == [SID, SID]


def test_a_rolled_back_insert_seen_inside_its_transaction_is_forgotten(engine):
    chat = Conversation(engine)
    chat.turn("r1")
    before = _revision(engine)
    with OrmSession(engine) as writer:
        writer.add(Request(id="ghost", session_id=SID, session_seq=9, timestamp=T0,
                           provider="anthropic", endpoint="/v1/messages",
                           context_fidelity="complete", tokens_total_input=1, tokens_total_output=1))
        writer.flush()
        assert crud.get_session_lineage_revision(writer, SID) != before  # sees its own row
        writer.rollback()
    assert _revision(engine) == before == _exact(engine)


def test_a_delete_from_another_process_is_seen_when_the_count_does_not_add_up(engine, full_runs):
    chat = Conversation(engine)
    for index in range(1, 4):
        chat.turn(f"r{index}", parent=f"r{index - 1}" if index > 1 else None)
    _revision(engine)
    outside = create_engine(engine.url)  # no events: another process
    with OrmSession(outside) as db:
        db.execute(text("delete from blocks where request_id = 'r2'"))
        db.execute(text("delete from requests where id = 'r2'"))
        db.commit()
    outside.dispose()
    chat.turn("r4", parent="r3")  # an append here, so the count alone looks plausible
    assert _revision(engine) == _exact(engine)
    assert full_runs == [SID, SID]


def test_the_incremental_base_expires_with_the_ttl(engine, full_runs, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    chat = Conversation(engine)
    chat.turn("r1")
    _revision(engine)
    clock[0] += service.REVISION_TTL_SECONDS + 1
    chat.turn("r2", parent="r1")
    assert _revision(engine) == _exact(engine)
    assert full_runs == [SID, SID]


def test_an_append_reloads_only_the_new_snapshot(engine, loaded):
    chat = Conversation(engine)
    for index in range(1, 6):
        chat.turn(f"r{index}", parent=f"r{index - 1}" if index > 1 else None, exact=index % 2 == 0)
    _graph(engine)
    assert loaded == [["r1", "r2", "r3", "r4", "r5"]]
    chat.turn("r6", parent="r5")
    _graph(engine)
    assert loaded[-1] == ["r6"]


def test_the_cached_graph_equals_a_full_build_after_every_step(engine):
    """Appends (exact and inferred turns, a rewind, a parallel stream), a block added to an
    old request, an in-place edit and a delete: the graph built from reused snapshots and the
    build memo matches a fresh build every time."""
    chat = Conversation(engine)
    steps = [
        lambda: chat.turn("a1"),
        lambda: chat.turn("a2", parent="a1"),
        lambda: chat.turn("a3", parent="a2", exact=True),
        lambda: chat.turn("a4", parent="a3"),
        lambda: chat.turn("b1", new=("other", "topic")),
        lambda: chat.turn("a5", parent="a3"),  # rewind to an earlier turn
        lambda: chat.turn("b2", parent="b1", exact=True),
        lambda: chat.turn("a6", parent="a5", exact=True),
        lambda: _write(engine, "insert into blocks (request_id, direction, position, block_type, "
                               "content_hash, token_count) values ('a2', 'output', 1, "
                               "'assistant_message', 'a2-late', 3)"),
        lambda: chat.turn("a7", parent="a6"),
        lambda: _write(engine, "update blocks set content_hash = 'edited' where request_id = 'b1' "
                               "and position = 1"),
        lambda: chat.turn("b3", parent="b2"),
        lambda: _write(engine, "delete from requests where id = 'a4'"),
        lambda: chat.turn("a8", parent="a7", exact=True),
    ]
    edges_seen = set()
    for step in steps:
        step()
        cached = _graph(engine)
        assert _same(cached, _fresh_graph(engine))
        assert _revision(engine) == _exact(engine)
        edges_seen.update(edge["certainty"] for edge in cached["edges"])
    assert {"exact", "inferred"} <= edges_seen


def test_the_build_memo_does_not_change_the_graph():
    from contextspy.analysis.context_diff import ContextBlock
    from contextspy.analysis.lineage_types import RequestSnapshot

    def snapshot(rid, seq, contents, output, predecessor=None):
        blocks = [ContextBlock(seq * 100, rid, "input", 0, None, "system_prompt", None, "system", 50)]
        blocks += [ContextBlock(seq * 100 + i, rid, "input", i, i, "user_message", None, c, 20)
                   for i, c in enumerate(contents, start=1)]
        blocks.append(ContextBlock(seq * 100 + 99, rid, "output", 0, None, "assistant_message", None, output, 5))
        return RequestSnapshot(
            id=rid, session_id=SID, session_seq=seq, timestamp=T0 + timedelta(seconds=seq),
            started_at=None, duration_ms=None, provider="anthropic", model=None, agent=None,
            endpoint="/v1/messages", provider_response_id=f"resp-{rid}",
            predecessor_response_id=f"resp-{predecessor}" if predecessor else None,
            context_fidelity="complete", tokens_total_input=1, tokens_total_output=1,
            blocks=tuple(blocks))

    requests = [
        snapshot("r1", 1, ["u1"], "o1"),
        snapshot("r2", 2, ["u1", "o1", "u2"], "o2", predecessor="r1"),
        snapshot("r3", 3, ["u1", "o1", "u2", "o2", "u3"], "o3"),
        snapshot("r4", 4, ["u1", "o1", "u4"], "o4"),
    ]
    memo = LineageBuildMemo()
    plain = build_lineage_graph(requests)
    assert _same(build_lineage_graph(requests, memo=memo), plain)
    assert _same(build_lineage_graph(requests, memo=memo), plain)  # served from the memo
    grown = requests + [snapshot("r5", 5, ["u1", "o1", "u2", "o2", "u3", "o3", "u5"], "o5", predecessor="r3")]
    assert _same(build_lineage_graph(grown, memo=memo), build_lineage_graph(grown))
    memo.retain(grown[1:])
    assert all(entry[0] is not requests[0] for entry in memo._contexts.values())


@pytest.mark.parametrize("statement,expected", [
    ("INSERT INTO requests (id, session_id) VALUES (?, ?)", True),
    ('INSERT INTO "blocks" (request_id, direction) VALUES (?, ?)', True),
    ("INSERT INTO blocks (request_id) SELECT id FROM requests", True),
    ("INSERT INTO blocks (id, request_id) VALUES (?, ?)", False),
    ("INSERT INTO requests (rowid, id) VALUES (?, ?)", False),
    ("INSERT OR REPLACE INTO requests (id) VALUES (?)", False),
    ("INSERT OR IGNORE INTO blocks (request_id) VALUES (?)", False),
    ("INSERT INTO requests (id) VALUES (?) ON CONFLICT (id) DO UPDATE SET model = ?", False),
    ("INSERT INTO blocks SELECT * FROM other", False),
    ("UPDATE requests SET model = ?", False),
    ("DELETE FROM blocks WHERE id = ?", False),
])
def test_only_plain_inserts_are_appends(statement, expected):
    assert service._appends_only(statement) is expected
