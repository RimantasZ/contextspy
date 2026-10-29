# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Provider-neutral, occurrence-aware diffs between invocation contexts."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from contextspy.analysis.blocks import BlockType, Direction


_CONFIG_TYPES = {BlockType.SYSTEM_PROMPT, BlockType.TOOL_DEFINITION}


@dataclass(frozen=True)
class ContextBlock:
    """The retained block metadata needed for lineage analysis."""

    id: int
    request_id: str
    direction: str
    position: int
    message_index: int | None
    block_type: str
    category: str | None
    content_hash: str | None
    token_count: int
    tool_name: str | None = None
    tool_call_id: str | None = None

    @property
    def is_configuration(self) -> bool:
        return self.block_type in _CONFIG_TYPES

    @property
    def is_meaningful(self) -> bool:
        return self.content_hash is not None or self.tool_call_id is not None


@dataclass(frozen=True)
class BlockMapping:
    parent_block_id: int
    child_block_id: int

    def to_dict(self) -> dict[str, int]:
        return {
            "parent_block_id": self.parent_block_id,
            "child_block_id": self.child_block_id,
        }


@dataclass(frozen=True)
class BlockReplacement(BlockMapping):
    slot: str

    def to_dict(self) -> dict[str, int | str]:
        return {**super().to_dict(), "slot": self.slot}


@dataclass
class ContextDelta:
    persisted: list[BlockMapping] = field(default_factory=list)
    promoted: list[BlockMapping] = field(default_factory=list)
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    replaced: list[BlockReplacement] = field(default_factory=list)
    unavailable_parent_blocks: list[int] = field(default_factory=list)
    unavailable_child_blocks: list[int] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_mappings: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {"summary": self.summary}
        if include_mappings:
            value.update({
                "persisted": [item.to_dict() for item in self.persisted],
                "promoted": [item.to_dict() for item in self.promoted],
                "added": self.added,
                "removed": self.removed,
                "replaced": [item.to_dict() for item in self.replaced],
                "unavailable_parent_blocks": self.unavailable_parent_blocks,
                "unavailable_child_blocks": self.unavailable_child_blocks,
            })
        return value


def semantic_key(block: ContextBlock) -> tuple[str, str | None, str | None, str | None] | None:
    """Return a role-sensitive identity that is stable across message-index shifts."""
    if block.content_hash is None and block.tool_call_id is None:
        return None
    return (
        block.block_type,
        block.content_hash,
        block.tool_name,
        block.tool_call_id,
    )


def _ordered(blocks: Iterable[ContextBlock], direction: str) -> list[ContextBlock]:
    return sorted(
        (block for block in blocks if block.direction == direction),
        key=lambda block: (block.position, block.id),
    )


def _slot_key(block: ContextBlock, ordinal: int) -> tuple[str, str]:
    if block.block_type == BlockType.TOOL_DEFINITION and block.tool_name:
        return block.block_type, block.tool_name
    return block.block_type, str(ordinal)


def _configuration_slots(blocks: list[ContextBlock]) -> dict[tuple[str, str], ContextBlock]:
    counters: dict[str, int] = {}
    result: dict[tuple[str, str], ContextBlock] = {}
    for block in blocks:
        if not block.is_configuration:
            continue
        ordinal = counters.get(block.block_type, 0)
        counters[block.block_type] = ordinal + 1
        result[_slot_key(block, ordinal)] = block
    return result


def _lcs_mappings(
    parent_keys: list, parent_ids: list[int], child_keys: list, child_ids: list[int],
) -> list[BlockMapping]:
    """Order-preserving alignment of two sequences of fingerprints.

    Blocks without a fingerprint never reach here: as equal ``None`` values they would
    align with each other and hide meaningful matches around them. The ids ride beside
    the keys so duplicate content keeps an explicit occurrence mapping.
    """
    if not parent_keys or not child_keys:
        return []
    parent_size, child_size = len(parent_keys), len(child_keys)
    # Exact shortcuts for the common shapes. When one sequence is a prefix of the other
    # (an unchanged context, or a conversation that only grew) SequenceMatcher's longest
    # match is the whole shorter sequence at offset 0, so the result is positional.
    # tests/test_lineage.py checks this against SequenceMatcher on random sequences.
    if parent_size <= child_size and child_keys[:parent_size] == parent_keys:
        return [BlockMapping(parent_block_id=left, child_block_id=right)
                for left, right in zip(parent_ids, child_ids)]
    if child_size < parent_size and parent_keys[:child_size] == child_keys:
        return [BlockMapping(parent_block_id=left, child_block_id=right)
                for left, right in zip(parent_ids, child_ids)]
    matcher = SequenceMatcher(a=parent_keys, b=child_keys, autojunk=False)
    mappings: list[BlockMapping] = []
    for parent_start, child_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            mappings.append(BlockMapping(
                parent_block_id=parent_ids[parent_start + offset],
                child_block_id=child_ids[child_start + offset],
            ))
    return mappings


def _counts(
    blocks_by_id: Mapping[int, ContextBlock], block_ids: Iterable[int],
) -> dict[str, Any]:
    blocks = [blocks_by_id[block_id] for block_id in block_ids if block_id in blocks_by_id]
    by_category: dict[str, dict[str, int]] = {}
    by_type: dict[str, dict[str, int]] = {}
    for block in blocks:
        category = block.category or "uncategorized"
        category_row = by_category.setdefault(category, {"blocks": 0, "tokens": 0})
        category_row["blocks"] += 1
        category_row["tokens"] += block.token_count
        type_row = by_type.setdefault(block.block_type, {"blocks": 0, "tokens": 0})
        type_row["blocks"] += 1
        type_row["tokens"] += block.token_count
    return {
        "blocks": len(blocks),
        "tokens": sum(block.token_count for block in blocks),
        "by_category": by_category,
        "by_block_type": by_type,
    }


class PreparedContext:
    """Everything ``diff_prepared`` needs from one request, computed once.

    Comparing a request with many candidates would otherwise re-sort its blocks, rebuild
    its configuration slots and recompute every fingerprint for each pair.
    """

    __slots__ = (
        "blocks", "by_id", "input", "output", "config_slots",
        "transcript_keys", "transcript_ids", "output_keys", "output_ids",
    )

    def __init__(self, blocks: Iterable[ContextBlock]) -> None:
        self.blocks = list(blocks)
        self.by_id = {block.id: block for block in self.blocks}
        self.input = _ordered(self.blocks, Direction.INPUT)
        self.output = _ordered(self.blocks, Direction.OUTPUT)
        self.config_slots = _configuration_slots(self.input)
        self.transcript_keys, self.transcript_ids = _keyed(
            block for block in self.input if not block.is_configuration
        )
        self.output_keys, self.output_ids = _keyed(self.output)


def _keyed(blocks: Iterable[ContextBlock]) -> tuple[list, list[int]]:
    keys: list = []
    ids: list[int] = []
    for block in blocks:
        key = semantic_key(block)
        if key is not None:
            keys.append(key)
            ids.append(block.id)
    return keys, ids


def prepare_context(blocks: Iterable[ContextBlock]) -> PreparedContext:
    return PreparedContext(blocks)


def diff_contexts(
    parent_blocks: Iterable[ContextBlock], child_blocks: Iterable[ContextBlock],
    *, detail: bool = True,
) -> ContextDelta:
    """Compare a parent input/output snapshot with a child's input context.

    ``detail=False`` returns only the ``persisted``, ``promoted`` and ``replaced`` mappings
    (what candidate scoring reads) and leaves ``added``, ``removed``, the unavailable lists
    and ``summary`` empty.
    """
    return diff_prepared(
        prepare_context(parent_blocks), prepare_context(child_blocks), detail=detail,
    )


def configuration_delta(
    parent: PreparedContext, child: PreparedContext,
) -> tuple[list[tuple[ContextBlock, ContextBlock]], list[BlockReplacement], set[int], set[int]]:
    """Compare system prompts and tool definitions slot by slot.

    Returns the unchanged pairs, the replaced ones, and the ids of every block of the two
    sides that took part (they are not transcript).
    """
    persisted: list[tuple[ContextBlock, ContextBlock]] = []
    replaced: list[BlockReplacement] = []
    consumed_parent: set[int] = set()
    consumed_child: set[int] = set()
    parent_config, child_config = parent.config_slots, child.config_slots
    for slot in sorted(parent_config.keys() & child_config.keys()):
        parent_block = parent_config[slot]
        child_block = child_config[slot]
        parent_key = semantic_key(parent_block)
        child_key = semantic_key(child_block)
        if parent_key is not None and parent_key == child_key:
            persisted.append((parent_block, child_block))
        elif parent_key is not None and child_key is not None:
            replaced.append(BlockReplacement(
                parent_block.id, child_block.id, slot=f"{slot[0]}:{slot[1]}",
            ))
        # When either fingerprint is unavailable the pair remains explicitly
        # unavailable rather than also being claimed as a replacement.
        consumed_parent.add(parent_block.id)
        consumed_child.add(child_block.id)
    return persisted, replaced, consumed_parent, consumed_child


def diff_prepared(
    parent: PreparedContext, child: PreparedContext, *, detail: bool = True,
) -> ContextDelta:
    """``diff_contexts`` for contexts that were prepared once and compared many times."""
    parent_input, child_input = parent.input, child.input

    config_persisted, config_replaced, consumed_parent, consumed_child = configuration_delta(
        parent, child,
    )
    delta = ContextDelta()
    delta.persisted.extend(
        BlockMapping(left.id, right.id) for left, right in config_persisted
    )
    delta.replaced.extend(config_replaced)

    persisted = _lcs_mappings(
        parent.transcript_keys, parent.transcript_ids,
        child.transcript_keys, child.transcript_ids,
    )
    delta.persisted.extend(persisted)
    consumed_parent.update(item.parent_block_id for item in persisted)
    consumed_child.update(item.child_block_id for item in persisted)

    if persisted:
        unmatched = [
            (key, block_id)
            for key, block_id in zip(child.transcript_keys, child.transcript_ids)
            if block_id not in consumed_child
        ]
        unmatched_keys = [key for key, _ in unmatched]
        unmatched_ids = [block_id for _, block_id in unmatched]
    else:
        unmatched_keys, unmatched_ids = child.transcript_keys, child.transcript_ids
    promoted = _lcs_mappings(
        parent.output_keys, parent.output_ids, unmatched_keys, unmatched_ids,
    )
    delta.promoted.extend(promoted)
    consumed_child.update(item.child_block_id for item in promoted)
    if not detail:
        return delta

    by_id = {block.id: block for block in parent.blocks + child.blocks}
    delta.removed = [
        block.id
        for block in parent_input
        if block.id not in consumed_parent and block.is_meaningful
    ]
    delta.added = [
        block.id
        for block in child_input
        if block.id not in consumed_child and block.is_meaningful
    ]
    delta.unavailable_parent_blocks = [
        block.id for block in parent_input if not block.is_meaningful
    ]
    delta.unavailable_child_blocks = [
        block.id for block in child_input if not block.is_meaningful
    ]

    persisted_child_ids = [item.child_block_id for item in delta.persisted]
    promoted_child_ids = [item.child_block_id for item in delta.promoted]
    replaced_parent_ids = [item.parent_block_id for item in delta.replaced]
    replaced_child_ids = [item.child_block_id for item in delta.replaced]
    delta.summary = {
        "persisted": _counts(by_id, persisted_child_ids),
        "promoted": _counts(by_id, promoted_child_ids),
        "added": _counts(by_id, delta.added),
        "removed": _counts(by_id, delta.removed),
        "replaced": {
            "blocks": len(delta.replaced),
            "tokens_before": sum(by_id[value].token_count for value in replaced_parent_ids),
            "tokens_after": sum(by_id[value].token_count for value in replaced_child_ids),
        },
    }
    return delta


def new_child_block_ids(
    delta: ContextDelta, child_blocks: Iterable[ContextBlock],
) -> list[int]:
    """Input blocks of the child that were not already present in the parent.

    Everything the diff did not map to a parent block is new: added blocks,
    blocks replaced in place, and blocks that could not be fingerprinted (so a
    "new only" view never hides something that could not be verified).
    """
    already_present = {item.child_block_id for item in delta.persisted}
    already_present.update(item.child_block_id for item in delta.promoted)
    return [
        block.id
        for block in _ordered(child_blocks, Direction.INPUT)
        if block.id not in already_present
    ]
