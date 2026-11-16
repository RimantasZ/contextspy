"""Large request bodies are stored as content-addressed parts and read back byte for byte."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from contextspy.config import Settings
from contextspy.db import compaction, crud, database, payload_store, session_archive
from contextspy.db.database import get_db, init_db
from contextspy.db.models import PayloadPart, Request
from contextspy.proxy.capture_writer import CaptureEnvelope, persist_capture

T0 = datetime(2026, 11, 16, 8, 0, 0, tzinfo=timezone.utc)


def message(i: int) -> dict:
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    return {
        "role": "user" if i % 2 == 0 else "assistant",
        "content": [{"type": "text", "text": f"turn {i}: " + " ".join(f"{words[(i + k) % 8]}{k} ünï" for k in range(120))}],
    }


def conversation(turns: int, **extra) -> dict:
    return {
        "model": "claude-test", "max_tokens": 1000, "stream": True,
        "system": [{"type": "text", "text": "You are a careful assistant. " * 20}],
        "tools": [{"name": f"tool_{i}", "description": "Does a thing. " * 20, "input_schema": {"type": "object"}} for i in range(3)],
        "messages": [message(i) for i in range(turns)],
        **extra,
    }


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "payload.db"
    init_db(path)
    yield path
    database.dispose_engine()


def capture(request_id: str, *, raw: str | None, canonical: str | None, session_id=None, minutes=0):
    """Persist through the capture writer, the path real traffic takes."""
    data = {
        "id": request_id, "session_id": session_id, "timestamp": T0 + timedelta(minutes=minutes),
        "provider": "anthropic", "endpoint": "/v1/messages", "provider_response_id": f"msg_{request_id}",
        "raw_request_body": raw, "canonical_request_body": canonical,
        "tokens_total_input": 10, "tokens_total_output": 2,
    }
    persist_capture(CaptureEnvelope(
        request_id=request_id, provider="anthropic", provider_response_id=f"msg_{request_id}",
        transport="http", data=data, blocks=(), tool_rows=(),
    ))


def stored(request_id: str) -> dict:
    with get_db() as db:
        row = db.execute(
            Request.__table__.select().where(Request.id == request_id)
        ).mappings().one()
        return dict(row)


def detail(request_id: str) -> dict:
    with get_db() as db:
        return crud.get_request(db, request_id).to_dict()


def part_count() -> int:
    with get_db() as db:
        return db.query(PayloadPart).count()


# --------------------------------------------------------------------------- pack / unpack


@pytest.mark.parametrize("dump", [
    lambda v: json.dumps(v, ensure_ascii=False),
    lambda v: json.dumps(v),                                        # \u escapes
    lambda v: json.dumps(v, separators=(",", ":")),                 # compact wire JSON
    lambda v: "\n  " + json.dumps(v, indent=2, ensure_ascii=False) + "\n",
])
def test_round_trip_is_byte_exact_for_any_formatting(dump):
    body = dump(conversation(40, metadata={"user_id": "u"}, empty=[], nothing=None))
    layout, parts = payload_store.pack(body)
    pieces = [layout["t"][0]]
    for digest, piece in zip(layout["p"], layout["t"][1:]):
        pieces += [parts[digest], piece]
    assert "".join(pieces) == body
    assert len(layout["t"]) == len(layout["p"]) + 1
    # each message, tool and system entry is its own part; short values stay in the layout
    assert len(layout["p"]) == 40 + 3 + 1
    assert all(len(content) >= payload_store.MIN_PART_CHARS for content in parts.values())


@pytest.mark.parametrize("body", [
    json.dumps(conversation(2)),                                     # below PACK_MIN_CHARS
    json.dumps([message(i) for i in range(60)]),                     # not an object
    json.dumps(conversation(60))[:-1],                               # truncated
    json.dumps(conversation(60)) + " trailing",                      # not a single document
    "x" * 100_000,
])
def test_bodies_that_are_small_or_not_one_json_object_stay_inline(body):
    assert payload_store.pack(body) is None


def test_consecutive_requests_share_every_repeated_part():
    first = payload_store.pack(json.dumps(conversation(60), ensure_ascii=False))[1]
    second = payload_store.pack(json.dumps(conversation(61), ensure_ascii=False))[1]
    assert len(set(second) - set(first)) == 1                        # only the new message


# --------------------------------------------------------------------------- capture and reads


def test_captured_bodies_are_stored_packed_and_returned_exactly(db_path):
    canonical = json.dumps(conversation(60), ensure_ascii=False)
    raw = json.dumps(conversation(60), indent=2)
    capture("a", raw=raw, canonical=canonical)

    row = stored("a")
    assert row["raw_request_body"] is None and row["canonical_request_body"] is None
    assert len(row["canonical_request_parts"]) < len(canonical) / 10
    d = detail("a")
    assert d["canonical_request_body"] == canonical
    assert d["raw_request_body"] == raw
    assert d["request_body"] == canonical
    assert d["content_state"] == "retained"


def test_small_bodies_stay_inline(db_path):
    capture("a", raw='{"thread": {"type": "continue"}}', canonical=None)
    row = stored("a")
    assert row["raw_request_body"] == '{"thread": {"type": "continue"}}'
    assert row["raw_request_parts"] is None
    assert detail("a")["raw_request_body"] == '{"thread": {"type": "continue"}}'


def test_a_growing_conversation_stores_only_its_new_parts(db_path):
    capture("a", raw=None, canonical=json.dumps(conversation(60), ensure_ascii=False))
    before = part_count()
    capture("b", raw=None, canonical=json.dumps(conversation(61), ensure_ascii=False), minutes=1)
    assert part_count() == before + 1


def test_the_lineage_repository_expands_a_packed_predecessor(db_path):
    from contextspy.proxy.addon import _DatabaseLineageRepository

    canonical = json.dumps(conversation(60), ensure_ascii=False)
    capture("a", raw=None, canonical=canonical)
    with get_db() as db:
        db.execute(Request.__table__.update().where(Request.id == "a").values(canonical_response_body='{"content": []}'))
    persisted = _DatabaseLineageRepository().get("anthropic", "msg_a")
    assert persisted is not None and persisted.request.text == canonical


def test_rows_stored_whole_are_still_read_directly(db_path):
    canonical = json.dumps(conversation(60), ensure_ascii=False)
    with get_db() as db:   # an older capture, or a row a migration rewrote
        crud.create_request(db, {
            "id": "old", "timestamp": T0, "provider": "anthropic", "endpoint": "/v1/messages",
            "canonical_request_body": canonical,
        })
    assert stored("old")["canonical_request_body"] == canonical
    assert detail("old")["canonical_request_body"] == canonical


def test_a_missing_part_reads_as_no_body_instead_of_a_corrupt_one(db_path, caplog):
    capture("a", raw=None, canonical=json.dumps(conversation(60), ensure_ascii=False))
    with get_db() as db:
        db.execute(PayloadPart.__table__.delete().where(PayloadPart.hash == db.query(PayloadPart.hash).limit(1).scalar_subquery()))
    assert detail("a")["canonical_request_body"] is None
    assert "Payload parts missing" in caplog.text


# --------------------------------------------------------------------------- cleanup


def test_archive_removes_the_parts_only_its_session_used(db_path):
    with get_db() as db:
        old = crud.create_session(db, "old").id
        crud.end_session(db, old)
        kept = crud.create_session(db, "kept").id
    shared = conversation(60)
    capture("a", raw=None, canonical=json.dumps(shared, ensure_ascii=False), session_id=old)
    capture("b", raw=None, canonical=json.dumps({**shared, "messages": shared["messages"] + [message(999)]}, ensure_ascii=False),
            session_id=old, minutes=1)
    capture("c", raw=None, canonical=json.dumps(conversation(30), ensure_ascii=False), session_id=kept, minutes=2)
    with get_db() as db:
        kept_parts = set(payload_store.hashes_of(db, "r.id = 'c'", {}))
        old_parts = set(payload_store.hashes_of(db, "r.session_id = :sid", {"sid": old}))
        freed_bytes = sum(len(p.content.encode()) for p in db.query(PayloadPart) if p.hash in old_parts - kept_parts)
    layout_bytes = sum(len(stored(r)["canonical_request_parts"].encode()) for r in ("a", "b"))

    with get_db() as db:
        outcome = session_archive.archive_session_data(db, old)

    with get_db() as db:
        remaining = {p.hash for p in db.query(PayloadPart)}
    assert remaining == kept_parts                       # the shared parts stay for "c"
    assert stored("a")["canonical_request_parts"] is None
    assert detail("c")["canonical_request_body"] == json.dumps(conversation(30), ensure_ascii=False)
    assert outcome["freed"]["request_body_bytes"] == layout_bytes + freed_bytes


def test_deleting_a_session_with_its_requests_removes_their_parts(db_path):
    with get_db() as db:
        sid = crud.create_session(db, "s").id
    capture("a", raw=None, canonical=json.dumps(conversation(60), ensure_ascii=False), session_id=sid)
    assert part_count() > 0
    with get_db() as db:
        crud.delete_session_with_requests(db, sid)
    assert part_count() == 0


def test_the_time_based_purge_removes_layouts_and_parts(db_path):
    capture("a", raw=None, canonical=json.dumps(conversation(60), ensure_ascii=False), minutes=-60 * 24 * 30)
    settings = Settings(config_dir=db_path.parent)
    settings.retention.raw_body_days = 7
    database.startup_vacuum(settings)
    assert stored("a")["canonical_request_parts"] is None
    assert part_count() == 0


# --------------------------------------------------------------------------- db-compact


def test_db_compact_packs_rows_stored_whole_without_changing_a_byte(db_path):
    bodies = {
        f"r{i}": (json.dumps(conversation(40 + i), indent=1), json.dumps(conversation(40 + i), ensure_ascii=False))
        for i in range(5)
    }
    bodies["small"] = ('{"a": 1}', None)
    with get_db() as db:
        for request_id, (raw, canonical) in bodies.items():
            crud.create_request(db, {
                "id": request_id, "timestamp": T0, "provider": "anthropic", "endpoint": "/v1/messages",
                "raw_request_body": raw, "canonical_request_body": canonical,
            })
    database.dispose_engine()

    assert compaction.count_packable(db_path) == 5
    outcome = compaction.compact_database(db_path)
    assert outcome.status == "compacted" and outcome.before.packable_requests == 5
    assert compaction.count_packable(db_path) == 0

    init_db(db_path)
    for request_id, (raw, canonical) in bodies.items():
        d = detail(request_id)
        assert (d["raw_request_body"], d["canonical_request_body"]) == (raw, canonical)
    assert stored("r0")["canonical_request_body"] is None and stored("small")["raw_request_body"] == '{"a": 1}'
    database.dispose_engine()
    assert compaction.compact_database(db_path).status == "already_compact"


def test_db_compact_packs_a_database_the_new_version_never_opened(tmp_path):
    path = tmp_path / "old.db"
    canonical = json.dumps(conversation(60), ensure_ascii=False)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, raw_request_body TEXT, canonical_request_body TEXT)")
        conn.execute("INSERT INTO requests VALUES ('a', NULL, ?)", (canonical,))
    assert compaction.pack_request_bodies(path) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT canonical_request_body FROM requests").fetchone() == (None,)
        assert conn.execute("SELECT COUNT(*) FROM payload_parts").fetchone()[0] == 60 + 3 + 1
