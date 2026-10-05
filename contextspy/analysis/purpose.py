# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Infer the main purpose of a request, and stamp every block with its ``source_key``.

The baseline is structural and works for every provider: it looks at the last input message
(trailing user text vs trailing tool results) and at what the response contained. Agent-specific
detectors (compaction, housekeeping side calls, ...) can be registered; none ships yet because
they need captures from each agent. See plans/wi0-data-foundation.md section 5.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

from contextspy.analysis.blocks import AnalyzedRequest, BlockType, BlockView
from contextspy.analysis.sources import resolve_sources

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = 1

# Emitted by the baseline: user_turn, tool_continuation, unknown, and compaction when the request's
# last item is an explicit provider compaction trigger. housekeeping is reserved for agent
# detectors. Readers must tolerate every value.
PURPOSES = ("user_turn", "tool_continuation", "compaction", "housekeeping", "unknown")

_MAX_NAMES = 20


@dataclass(frozen=True)
class PurposeInputs:
    agent: str | None
    input_blocks: Sequence[BlockView]
    output_blocks: Sequence[BlockView]
    has_response: bool


@dataclass(frozen=True)
class PurposeResult:
    purpose: str
    detail: dict = field(default_factory=dict)


PurposeDetector = Callable[[PurposeInputs], "PurposeResult | None"]
_DETECTORS: list[tuple[frozenset[str] | None, PurposeDetector]] = []


def register_purpose_detector(*, agents: frozenset[str] | None, detector: PurposeDetector) -> None:
    """Register an agent-specific detector; it runs before the baseline and may return any purpose."""
    _DETECTORS.append((agents, detector))


@dataclass(frozen=True)
class RequestClassification:
    purpose: str | None
    purpose_detail: dict | None
    classifier_version: int

    def to_db_fields(self) -> dict:
        return {
            "purpose": self.purpose,
            "purpose_detail": json.dumps(self.purpose_detail) if self.purpose_detail else None,
            "classifier_version": self.classifier_version,
        }


def _unique(names: Sequence[str | None]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
        if len(seen) >= _MAX_NAMES:
            break
    return seen


def _has_text(block: BlockView) -> bool:
    """Non-empty text, also for stored rows whose content was not loaded (they carry a hash)."""
    if block.content is not None:
        return bool(block.content.strip())
    return bool(getattr(block, "content_hash", None))


def _baseline_purpose(inputs: PurposeInputs) -> PurposeResult:
    detail: dict = {}
    blocks = list(inputs.input_blocks)

    # Messages are the blocks with a non-negative index; tool definitions and the top-level system
    # prompt are structural. Instruction messages (system/developer role) and reasoning items are not
    # part of the conversation's tail: providers append them after the real last turn, and
    # a request that ends in one still continues whatever came before it.
    by_message: dict[int, list[BlockView]] = {}
    for block in blocks:
        if (
            block.message_index is not None and block.message_index >= 0
            and block.block_type not in (BlockType.SYSTEM_PROMPT, BlockType.THINKING)
        ):
            by_message.setdefault(block.message_index, []).append(block)

    call_names = {
        block.tool_call_id: block.tool_name
        for block in [*blocks, *inputs.output_blocks]
        if block.block_type == BlockType.TOOL_CALL and block.tool_call_id and block.tool_name
    }

    purpose = "unknown"
    if by_message:
        last = max(by_message)
        tail = by_message[last]
        has_user_text = any(
            b.block_type == BlockType.USER_MESSAGE and not b.attrs.get("is_prefill") for b in tail
        )
        if any(b.attrs.get("provider_item_type") == "compaction_trigger" for b in tail):
            # An explicit wire-format item (OpenAI Responses) asking the provider to compact the context.
            purpose = "compaction"
        elif any(b.block_type == BlockType.TOOL_RESULT for b in tail):
            purpose = "tool_continuation"
            # Parallel results can arrive as several consecutive messages: take the whole trailing run.
            names: list[str | None] = []
            for index in sorted(by_message, reverse=True):
                results = [b for b in by_message[index] if b.block_type == BlockType.TOOL_RESULT]
                if not results:
                    break
                names.extend(call_names.get(b.tool_call_id) or b.tool_name for b in reversed(results))
            ordered = _unique(list(reversed(names)))
            if ordered:
                detail["trailing_tool_results"] = ordered
            detail["has_user_text"] = has_user_text
        elif has_user_text:
            purpose = "user_turn"

    if inputs.has_response:
        outputs = inputs.output_blocks
        calls = [b for b in outputs if b.block_type == BlockType.TOOL_CALL]
        has_text = any(b.block_type == BlockType.ASSISTANT_MESSAGE and _has_text(b) for b in outputs)
        kind = (
            "mixed" if calls and has_text else "tool_calls" if calls
            else "final_text" if has_text else "empty"
        )
        response: dict = {"kind": kind}
        called = _unique([b.tool_name for b in calls])
        if called:
            response["tool_calls"] = called
        detail["response"] = response
    return PurposeResult(purpose, detail)


def derive_purpose(inputs: PurposeInputs) -> PurposeResult:
    """Run registered detectors for the agent, then fall back to the structural baseline."""
    baseline = _baseline_purpose(inputs)
    for agents, detector in _DETECTORS:
        if agents is not None and inputs.agent not in agents:
            continue
        try:
            result = detector(inputs)
        except Exception:
            logger.debug("purpose detector failed", exc_info=True)
            continue
        if result is not None:
            return PurposeResult(result.purpose, {**baseline.detail, **result.detail})
    return baseline


def classify_request(
    analyzed: AnalyzedRequest, *, agent: str | None, has_response: bool,
) -> RequestClassification | None:
    """Stamp ``source_key`` (and ``attrs["source"]``) on every block and derive the request purpose.

    Returns None when the analysis produced no blocks at all (the request could not be parsed),
    so such rows stay unclassified instead of being stamped ``unknown``. Never raises: on failure
    the purpose fields are left empty and whatever was already set stays.
    """
    blocks = [*analyzed.input_blocks, *analyzed.output_blocks]
    if not blocks:
        return None
    try:
        for block, info in zip(blocks, resolve_sources(blocks, agent=agent)):
            block.source_key = info.key
            if info.detail:
                block.attrs["source"] = info.detail
        result = derive_purpose(PurposeInputs(
            agent=agent, input_blocks=analyzed.input_blocks,
            output_blocks=analyzed.output_blocks, has_response=has_response,
        ))
        return RequestClassification(result.purpose, result.detail or None, CLASSIFIER_VERSION)
    except Exception:
        logger.warning("request classification failed", exc_info=True)
        return RequestClassification(None, None, CLASSIFIER_VERSION)
