# Copyright 2026 Rimantas Zukaitis
"""Per-request system-prompt headers (Claude Code's billing header) stay out of block identity."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from contextspy.analysis.adapters.anthropic import AnthropicAdapter
from contextspy.analysis.blocks import (
    Block, BlockType, Direction, content_hash, split_volatile_header,
)
from contextspy.analysis.context_diff import ContextBlock, diff_contexts, new_child_block_ids
from contextspy.analysis.tokenizer import count_tokens
from contextspy.db import crud, migrations
from contextspy.db.database import get_db, init_db
from contextspy.db.models import BlockContent, BlockRecord

BODY = "You are Claude Code, Anthropic's official CLI for Claude.\nFollow the rules."


def _header(n: int) -> str:
    return (f"x-anthropic-billing-header: cc_version=2.1.284.606; cch=7b4a{n}; "
            f"cc_prev_req=req_{n:04d}; cc_prompt_id=prompt-{n}; cc_turn_index={n};\n")


def test_split_volatile_header_cases():
    assert split_volatile_header(_header(1) + BODY) == (BODY, _header(1))
    assert split_volatile_header(_header(1)) == ("", _header(1))
    assert split_volatile_header(BODY) == (BODY, None)
    # Only a leading header is removed; the same text quoted later is left alone.
    quoted = BODY + "\n" + _header(1)
    assert split_volatile_header(quoted) == (quoted, None)
    crlf = "x-anthropic-billing-header: a=b;\r\n" + BODY
    assert split_volatile_header(crlf) == (BODY, "x-anthropic-billing-header: a=b;\r\n")
    assert split_volatile_header("") == ("", None)


def test_block_make_keeps_identity_stable_but_counts_the_full_text():
    first = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, _header(1) + BODY, message_index=-1)
    second = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, _header(2) + BODY, message_index=-1)
    assert first.content == second.content == BODY
    assert first.content_hash == second.content_hash == content_hash(BODY)
    assert first.token_count == count_tokens(_header(1) + BODY)
    assert first.attrs["volatile_header"] == _header(1)
    assert second.attrs["volatile_header"] == _header(2)
    # Existing attrs are preserved, and an explicit token_count still wins.
    kept = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, _header(3) + BODY,
                      attrs={"cache_control": {"type": "ephemeral"}}, token_count=5)
    assert kept.attrs == {"cache_control": {"type": "ephemeral"}, "volatile_header": _header(3)}
    assert kept.token_count == 5


def test_header_is_only_split_from_input_system_prompts():
    text = _header(1) + BODY
    for direction, block_type in ((Direction.INPUT, BlockType.USER_MESSAGE),
                                  (Direction.OUTPUT, BlockType.SYSTEM_PROMPT)):
        block = Block.make(direction, block_type, text)
        assert block.content == text and "volatile_header" not in block.attrs


def _anthropic_blocks(header_number: int) -> list[Block]:
    request = {
        "model": "claude-test",
        "system": [{"type": "text", "text": _header(header_number) + BODY,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "hello"}],
    }
    blocks, _ = AnthropicAdapter().parse_request(request)
    return blocks


def test_anthropic_requests_differing_only_in_the_header_share_the_system_block_hash():
    first, second = _anthropic_blocks(1), _anthropic_blocks(2)
    assert first[0].block_type == BlockType.SYSTEM_PROMPT
    assert first[0].content_hash == second[0].content_hash
    assert first[0].attrs["cache_control"] == {"type": "ephemeral"}
    assert first[0].attrs["volatile_header"] != second[0].attrs["volatile_header"]


def _context(request_id: str, blocks: list[Block], first_id: int) -> list[ContextBlock]:
    return [
        ContextBlock(id=first_id + i, request_id=request_id, direction=b.direction, position=i,
                     message_index=b.message_index, block_type=b.block_type, category=b.category,
                     content_hash=b.content_hash, token_count=b.token_count)
        for i, b in enumerate(blocks)
    ]


def test_context_diff_treats_the_system_prompt_as_persisted_not_new():
    parent = _context("p", _anthropic_blocks(1), 1)
    child = _context("c", _anthropic_blocks(2), 10)
    delta = diff_contexts(parent, child)
    assert delta.replaced == []
    assert [item.child_block_id for item in delta.persisted][0] == 10
    assert new_child_block_ids(delta, child) == []


# -- migration ---------------------------------------------------------------

def _request(db, request_id: str) -> None:
    crud.create_request(db, {
        "id": request_id, "timestamp": datetime.now(timezone.utc), "provider": "anthropic",
        "endpoint": "/v1/messages", "transport": "rest", "response_complete": 1,
    })


def _legacy_block(content: str, **attrs) -> Block:
    """A block as stored before v8: the full text, hashed with its per-request header."""
    return Block(direction=Direction.INPUT, block_type=BlockType.SYSTEM_PROMPT, content=content,
                 message_index=-1, content_hash=content_hash(content),
                 token_count=count_tokens(content), attrs=attrs)


def test_v8_rekeys_retained_system_prompts_and_is_idempotent(tmp_path):
    init_db(tmp_path / "v8.db")
    ids = [str(uuid.uuid4()) for _ in range(3)]
    quoted = BODY + "\n" + _header(9)
    with get_db() as db:
        for number, request_id in enumerate(ids, start=1):
            _request(db, request_id)
            crud.insert_blocks(db, request_id, [
                _legacy_block(_header(number) + BODY, cache_control={"type": "ephemeral"}),
                _legacy_block(quoted),
            ])
        # A purged system block: hash retained, content gone.
        purged_hash = content_hash("purged " + _header(4) + BODY)
        db.add(BlockRecord(request_id=ids[0], direction="input", position=9, block_type="system_prompt",
                           content_hash=purged_hash, token_count=7))
        db.flush()
        original_tokens = {r.id: r.token_count for r in db.execute(select(BlockRecord)).scalars()}

    for _ in range(2):
        with get_db() as db:
            migrations._migrate_to_v8(db)

    with get_db() as db:
        records = db.execute(select(BlockRecord).order_by(BlockRecord.id)).scalars().all()
        rekeyed = [r for r in records if r.position == 0]
        assert {r.content_hash for r in rekeyed} == {content_hash(BODY)}
        assert [json.loads(r.attrs)["volatile_header"] for r in rekeyed] == [_header(n) for n in (1, 2, 3)]
        assert all(json.loads(r.attrs)["cache_control"] == {"type": "ephemeral"} for r in rekeyed)
        # Token counts describe what was sent and do not change.
        assert {r.id: r.token_count for r in records} == original_tokens
        # Duplicates collapse to one stable content row; the per-request copies are gone.
        contents = {c.hash: c.content for c in db.execute(select(BlockContent)).scalars()}
        assert contents[content_hash(BODY)] == BODY
        assert not any(c.startswith("x-anthropic-billing-header") for c in contents.values())
        # A header that is not at the start, and a purged block, are left alone.
        untouched = [r for r in records if r.position == 1]
        assert {r.content_hash for r in untouched} == {content_hash(quoted)}
        assert all(r.attrs is None for r in untouched)
        assert next(r for r in records if r.position == 9).content_hash == purged_hash


def test_v8_is_registered_and_pending_for_older_databases():
    assert migrations.SCHEMA_VERSION == 8
    assert 8 in migrations._DATA_MIGRATIONS
