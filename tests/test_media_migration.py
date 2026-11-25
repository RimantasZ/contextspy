"""Schema v11 re-analyzes stored media blocks in place."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import Block, BlockType, Direction
from contextspy.analysis.classifier import classify_blocks
from contextspy.db import crud, migrations
from contextspy.db.database import get_db, init_db
from contextspy.db.models import BlockContent, BlockRecord
from tests.test_media import anthropic_image, png

BODY = {"model": "claude-sonnet-5-5", "messages": [
    {"role": "user", "content": "please look"},
    {"role": "assistant", "content": "send it"},
    {"role": "user", "content": [anthropic_image(png(2000, 1224))]},
]}


def _old_blocks() -> list[Block]:
    """The rows as stored before v11: the image was an `other` block holding "[image]"."""
    blocks, _ = get_adapter("/v1/messages").parse_request(BODY)
    blocks[-1] = Block.make(
        Direction.INPUT, BlockType.OTHER, "[image]", message_index=2,
        attrs={"contains_media": True, "token_estimate": "text_only", "content_type": "image"},
        json_path=("messages", 2, "content", 0),
    )
    classify_blocks(blocks)
    blocks[0].category = "current_user_message"  # what it was while the image did not count
    blocks[-1].category = "conversation_history"
    return blocks


@pytest.fixture
def store(tmp_path):
    init_db(tmp_path / "v11.db")

    def add(body=BODY, blocks=None) -> str:
        with get_db() as db:
            request_id = crud.create_request(db, {
                "id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "agent": "claude_code", "endpoint": "/v1/messages",
                "canonical_request_body": json.dumps(body) if body else None,
            }).id
            crud.insert_blocks(db, request_id, blocks or _old_blocks())
        return request_id

    return add


def _migrate() -> dict:
    with get_db() as db:
        migrations._migrate_to_v11(db)
        return dict(db.info["v11_backfill"])


def _records(db, request_id):
    return db.execute(
        select(BlockRecord).where(BlockRecord.request_id == request_id).order_by(BlockRecord.position)
    ).scalars().all()


def test_v11_rewrites_media_in_place_and_recomputes_totals(store):
    request_id = store()
    with get_db() as db:
        ids = [r.id for r in _records(db, request_id)]
    assert _migrate()["updated"] == 1
    with get_db() as db:
        records = _records(db, request_id)
        assert [r.id for r in records] == ids
        image = records[-1]
        assert (image.block_type, image.token_count, image.source_key) == ("user_message", 3168, "user")
        assert [r.category for r in records] == [
            "conversation_history", "conversation_history", "current_user_message",
        ]
        assert db.get(BlockContent, image.content_hash).content.startswith("[image png 2000×1224")
        assert db.execute(select(BlockContent).where(BlockContent.content == "[image]")).first() is None
        row = crud.get_request(db, request_id)
        assert row.tokens_current_user_message == 3168
        assert row.tokens_total_input == sum(r.token_count for r in records) + 4 * 3 + 3
        assert row.purpose == "user_turn"
    assert _migrate()["updated"] == 0  # idempotent


def test_v11_does_not_restore_purged_content(store):
    request_id = store()
    with get_db() as db:
        db.delete(db.get(BlockContent, _records(db, request_id)[-1].content_hash))
    assert _migrate()["updated"] == 1
    with get_db() as db:
        image = _records(db, request_id)[-1]
        assert image.token_count == 3168
        assert db.get(BlockContent, image.content_hash) is None


def test_v11_leaves_unmatched_and_bodyless_requests_alone(store):
    short = store(blocks=_old_blocks()[:1])  # no stored row for the image
    purged = store(body=None)
    stats = _migrate()
    assert (stats["updated"], stats["skipped_unmatched"], stats["without_body"]) == (0, 1, 1)
    with get_db() as db:
        assert len(_records(db, short)) == 1
        assert _records(db, purged)[-1].block_type == "other"
