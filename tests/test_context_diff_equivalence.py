# Copyright 2026 Rimantas Zukaitis
"""The optimised context diff must equal the original algorithm, kept here as the reference."""
from __future__ import annotations

import random
from difflib import SequenceMatcher

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.analysis.context_diff import (
    BlockMapping, BlockReplacement, ContextBlock, ContextDelta, _configuration_slots, _lcs_mappings,
    _ordered, diff_contexts, diff_prepared, prepare_context, semantic_key,
)

# --- original implementation (before blocks were prepared once per request) ---------------
def _reference_lcs(
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
    # The common unchanged-context case has the same occurrence order. The
    # positional mapping is exactly SequenceMatcher's full-match result, while
    # avoiding its index construction for every candidate in a dense session.
    if [key for key, _ in parent_values] == [key for key, _ in child_values]:
        return [BlockMapping(parent_block_id=left[1], child_block_id=right[1])
                for left, right in zip(parent_values, child_values)]
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


def reference_diff_contexts(
    parent_blocks: Iterable[ContextBlock], child_blocks: Iterable[ContextBlock],
    *, detail: bool = True,
) -> ContextDelta:
    """Compare a parent input/output snapshot with a child's input context.

    ``detail=False`` returns only the ``persisted``, ``promoted`` and ``replaced`` mappings
    (what candidate scoring reads) and leaves ``added``, ``removed``, the unavailable lists
    and ``summary`` empty.
    """
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
    persisted = _reference_lcs(parent_transcript, child_transcript)
    delta.persisted.extend(persisted)
    consumed_parent.update(item.parent_block_id for item in persisted)
    consumed_child.update(item.child_block_id for item in persisted)

    unmatched_child = [block for block in child_transcript if block.id not in consumed_child]
    promoted = _reference_lcs(parent_output, unmatched_child)
    delta.promoted.extend(promoted)
    consumed_child.update(item.child_block_id for item in promoted)
    if not detail:
        return delta

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





class _Keyed:
    """Just enough of a block for the reference LCS, which fingerprints blocks itself."""
    def __init__(self, key, block_id):
        self.id = block_id
        self.block_type, self.content_hash, self.tool_name, self.tool_call_id = "t", key, None, None


# --- random comparison --------------------------------------------------------------------
_TEXTS = [f"text-{i}" for i in range(6)]  # few values, so duplicates and reorderings are common


def _random_context(rng: random.Random, request_id: str, first_id: int) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    next_id = first_id

    def add(direction, block_type, content, position, tool_name=None, tool_call_id=None):
        nonlocal next_id
        blocks.append(ContextBlock(
            id=next_id, request_id=request_id, direction=direction, position=position,
            message_index=position, block_type=block_type, category=None,
            content_hash=content, token_count=rng.randint(1, 9),
            tool_name=tool_name, tool_call_id=tool_call_id,
        ))
        next_id += 1

    position = 0
    if rng.random() < 0.8:
        add(Direction.INPUT, BlockType.SYSTEM_PROMPT, rng.choice(_TEXTS[:2]), position)
        position += 1
    for name in rng.sample(["bash", "read", "edit"], rng.randint(0, 3)):
        add(Direction.INPUT, BlockType.TOOL_DEFINITION, rng.choice(_TEXTS[:2]), position, tool_name=name)
        position += 1
    for _ in range(rng.randint(0, 9)):
        kind = rng.choice([BlockType.USER_MESSAGE, BlockType.ASSISTANT_MESSAGE, BlockType.TOOL_RESULT])
        content = None if rng.random() < 0.15 else rng.choice(_TEXTS)  # unfingerprinted blocks
        call_id = f"call-{rng.randint(0, 2)}" if kind == BlockType.TOOL_RESULT else None
        add(Direction.INPUT, kind, content, position, tool_call_id=call_id)
        position += 1
    for index in range(rng.randint(0, 3)):
        add(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE,
            None if rng.random() < 0.1 else rng.choice(_TEXTS), index)
    rng.shuffle(blocks)  # block order in the input list must not matter
    return blocks


def _extend(rng: random.Random, parent: list[ContextBlock], request_id: str, first_id: int):
    """A child that grows, rewinds or edits its parent, so prefix shapes are well covered."""
    inputs = sorted((b for b in parent if b.direction == Direction.INPUT), key=lambda b: b.position)
    outputs = [b for b in parent if b.direction == Direction.OUTPUT]
    keep = inputs[: rng.randint(0, len(inputs))] if rng.random() < 0.4 else list(inputs)
    child, next_id = [], first_id
    for block in keep + outputs[: rng.randint(0, len(outputs))]:
        child.append(ContextBlock(
            id=next_id, request_id=request_id, direction=Direction.INPUT,
            position=len(child), message_index=len(child), block_type=block.block_type,
            category=None, content_hash=block.content_hash, token_count=block.token_count,
            tool_name=block.tool_name, tool_call_id=block.tool_call_id,
        ))
        next_id += 1
    for _ in range(rng.randint(0, 3)):
        child.append(ContextBlock(
            id=next_id, request_id=request_id, direction=Direction.INPUT,
            position=len(child), message_index=len(child), block_type=BlockType.USER_MESSAGE,
            category=None, content_hash=rng.choice(_TEXTS), token_count=3,
        ))
        next_id += 1
    return child


def _same_mappings(left: ContextDelta, right: ContextDelta) -> bool:
    return (left.persisted, left.promoted, left.replaced) == (right.persisted, right.promoted, right.replaced)


def test_prepared_diff_equals_the_original_algorithm_on_random_contexts():
    rng = random.Random(20261029)
    for case in range(3000):
        parent = _random_context(rng, "p", 1)
        child = (_extend(rng, parent, "c", 1000) if case % 2 else _random_context(rng, "c", 1000))
        expected = reference_diff_contexts(parent, child)
        assert diff_contexts(parent, child).to_dict() == expected.to_dict(), case
        assert _same_mappings(diff_contexts(parent, child, detail=False), expected), case


def test_prepared_contexts_can_be_compared_repeatedly():
    rng = random.Random(7)
    contexts = [_random_context(rng, f"r{i}", 100 * i + 1) for i in range(12)]
    prepared = [prepare_context(blocks) for blocks in contexts]
    for _ in range(2):  # comparing must not modify a prepared context
        for i, parent in enumerate(prepared):
            for j, child in enumerate(prepared):
                assert diff_prepared(parent, child).to_dict() == reference_diff_contexts(
                    contexts[i], contexts[j]).to_dict()


def test_sequence_matching_shortcuts_equal_sequence_matcher():
    rng = random.Random(11)
    for case in range(4000):
        parent = [rng.choice("abcd") for _ in range(rng.randint(0, 8))]
        if case % 3 == 0:  # prefix in one direction or the other
            child = parent + [rng.choice("abcd") for _ in range(rng.randint(0, 4))]
            if case % 2:
                parent, child = child, parent
        else:
            child = [rng.choice("abcd") for _ in range(rng.randint(0, 8))]
        parent_ids, child_ids = list(range(len(parent))), list(range(100, 100 + len(child)))
        assert _lcs_mappings(parent, parent_ids, child, child_ids) == _reference_lcs(
            [_Keyed(k, i) for k, i in zip(parent, parent_ids)],
            [_Keyed(k, i) for k, i in zip(child, child_ids)],
        ), (parent, child)
