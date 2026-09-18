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
    attrs: Mapping[str, Any] = field(default_factory=dict)

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
    parent: list[ContextBlock], child: list[ContextBlock],
) -> list[BlockMapping]:
    # Unmatchable structural blocks must not participate as equal ``None``
    # values because their alignment can hide meaningful matches around them.
    parent_values = [
        (key, block.id) for block in parent if (key := semantic_key(block)) is not None
    ]
    child_values = [
        (key, block.id) for block in child if (key := semantic_key(block)) is not None
    ]
    # IDs deliberately do not participate in equality. Keeping them beside the
    # key makes the occurrence mapping explicit after SequenceMatcher selects
    # an order-preserving alignment, including duplicate content occurrences.
    matcher = SequenceMatcher(
        a=[key for key, _ in parent_values],
        b=[key for key, _ in child_values],
        autojunk=False,
    )
    mappings: list[BlockMapping] = []
    for parent_start, child_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            mappings.append(BlockMapping(
                parent_block_id=parent_values[parent_start + offset][1],
                child_block_id=child_values[child_start + offset][1],
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


def diff_contexts(
    parent_blocks: Iterable[ContextBlock], child_blocks: Iterable[ContextBlock],
) -> ContextDelta:
    """Compare a parent input/output snapshot with a child's input context."""
    parent_all = list(parent_blocks)
    child_all = list(child_blocks)
    parent_input = _ordered(parent_all, Direction.INPUT)
    parent_output = _ordered(parent_all, Direction.OUTPUT)
    child_input = _ordered(child_all, Direction.INPUT)

    by_id = {block.id: block for block in parent_all + child_all}
    parent_config = _configuration_slots(parent_input)
    child_config = _configuration_slots(child_input)

    delta = ContextDelta()
    consumed_parent: set[int] = set()
    consumed_child: set[int] = set()

    for slot in sorted(parent_config.keys() & child_config.keys()):
        parent = parent_config[slot]
        child = child_config[slot]
        parent_key = semantic_key(parent)
        child_key = semantic_key(child)
        if parent_key is not None and parent_key == child_key:
            delta.persisted.append(BlockMapping(parent.id, child.id))
        elif parent_key is not None and child_key is not None:
            delta.replaced.append(BlockReplacement(
                parent.id, child.id, slot=f"{slot[0]}:{slot[1]}",
            ))
        # When either fingerprint is unavailable the pair remains explicitly
        # unavailable rather than also being claimed as a replacement.
        consumed_parent.add(parent.id)
        consumed_child.add(child.id)

    parent_transcript = [
        block for block in parent_input
        if block.id not in consumed_parent and not block.is_configuration
    ]
    child_transcript = [
        block for block in child_input
        if block.id not in consumed_child and not block.is_configuration
    ]
    persisted = _lcs_mappings(parent_transcript, child_transcript)
    delta.persisted.extend(persisted)
    consumed_parent.update(item.parent_block_id for item in persisted)
    consumed_child.update(item.child_block_id for item in persisted)

    unmatched_child = [block for block in child_transcript if block.id not in consumed_child]
    promoted = _lcs_mappings(parent_output, unmatched_child)
    delta.promoted.extend(promoted)
    consumed_child.update(item.child_block_id for item in promoted)

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
