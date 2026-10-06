"""Schema v9: request purpose, block source/location columns, session archive marker."""
import json
import sqlite3
from datetime import datetime, timezone

from contextspy.db import migrations

_V9_COLUMNS = {
    "requests": {"purpose", "purpose_detail", "classifier_version"},
    "blocks": {"source_key", "json_path"},
    "sessions": {"archived_at"},
}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(conn):
    return {row[1] for row in conn.execute("SELECT * FROM sqlite_master WHERE type = 'index'")}


def _v8_database(path):
    """Create a real database, then strip it back to the v8 shape (no v9 columns or indexes)."""
    from contextspy.db.database import init_db

    init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX IF EXISTS idx_blocks_source_key")
    conn.execute("DROP INDEX IF EXISTS idx_requests_session_purpose")
    for table, columns in _V9_COLUMNS.items():
        for column in columns:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    return conn


def test_schema_version_and_migration_are_registered():
    assert migrations.SCHEMA_VERSION >= 9
    assert 9 in migrations._DATA_MIGRATIONS


def test_migrate_adds_v9_columns_and_indexes_to_a_v8_database_idempotently(tmp_path):
    from contextspy.db import database

    path = tmp_path / "v8.db"
    conn = _v8_database(path)
    for table, columns in _V9_COLUMNS.items():
        assert not columns & _columns(conn, table)
    conn.close()

    database.init_db(path)
    database.init_db(path)  # second run must be a no-op, not an error

    with sqlite3.connect(path) as conn:
        for table, columns in _V9_COLUMNS.items():
            assert columns <= _columns(conn, table)
        assert {"idx_blocks_source_key", "idx_requests_session_purpose"} <= _indexes(conn)


def test_existing_rows_read_back_with_null_v9_values(tmp_path):
    """A request captured before the upgrade must serialise cleanly with NULL v9 fields."""
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db
    from contextspy.analysis.blocks import Block, BlockType, Direction

    init_db(tmp_path / "null.db")
    with get_db() as db:
        session = crud.create_session(db, "s")
        session_id = session.id
        crud.create_request(db, {
            "id": "old", "session_id": session_id,
            "timestamp": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "provider": "anthropic", "endpoint": "/v1/messages",
        })
        crud.insert_blocks(db, "old", [Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "hi", message_index=0)])

    with get_db() as db:
        request = crud.get_request(db, "old").to_dict(include_raw=False)
        assert request["purpose"] is None
        assert request["purpose_detail"] is None
        assert request["classifier_version"] is None
        block = crud.get_blocks(db, "old")[0]
        assert block["source_key"] is None
        assert block["json_path"] is None
        assert crud.get_session(db, session_id).to_dict()["archived_at"] is None


def test_v9_values_round_trip_through_to_dict(tmp_path):
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db
    from contextspy.db.models import BlockRecord

    init_db(tmp_path / "roundtrip.db")
    with get_db() as db:
        crud.create_request(db, {
            "id": "new",
            "timestamp": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "provider": "anthropic", "endpoint": "/v1/messages",
            "purpose": "tool_continuation",
            "purpose_detail": json.dumps({"trailing_tool_results": ["Read"]}),
            "classifier_version": 1,
        })
        db.add(BlockRecord(
            request_id="new", direction="input", position=0, block_type="tool_result",
            source_key="tool:Read", json_path=json.dumps(["messages", 3, "content", 1]),
        ))
        db.flush()

    with get_db() as db:
        request = crud.get_request(db, "new").to_dict(include_raw=False)
        assert request["purpose"] == "tool_continuation"
        assert request["purpose_detail"] == {"trailing_tool_results": ["Read"]}
        assert request["classifier_version"] == 1
        block = crud.get_blocks(db, "new")[0]
        assert block["source_key"] == "tool:Read"
        assert block["json_path"] == ["messages", 3, "content", 1]
