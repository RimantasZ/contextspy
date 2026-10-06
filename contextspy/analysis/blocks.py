# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Provider-agnostic block model — the currency of the analysis pipeline.

A ``Block`` is one content part of a request or response: a system prompt, a
tool definition, a single tool_result, one text/thinking segment, etc.
Adapters (``analysis/adapters/``) turn provider wire formats into blocks;
the classifier (``analysis/classifier.py``) assigns each input block a
semantic ``category``; ``db/crud.py`` persists them content-addressed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from contextspy.analysis.tokenizer import count_tokens


class BlockType(StrEnum):
    """Structural type — a fact about the wire format, independent of category."""

    SYSTEM_PROMPT = "system_prompt"
    TOOL_DEFINITION = "tool_definition"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ASSISTANT_PREFILL = "assistant_prefill"
    THINKING = "thinking"
    OTHER = "other"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


_BLOCK_VISUAL = {
    BlockType.SYSTEM_PROMPT: "system",
    BlockType.TOOL_DEFINITION: "tool_definition",
    BlockType.USER_MESSAGE: "user",
    BlockType.ASSISTANT_MESSAGE: "assistant",
    BlockType.TOOL_CALL: "tool_call",
    BlockType.TOOL_RESULT: "tool_result",
    BlockType.THINKING: "thinking",
    BlockType.ASSISTANT_PREFILL: "prefill",
}


def block_visual_token_totals(blocks: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Aggregate persisted block tokens for the request workbench's visual keys."""
    totals: dict[str, dict[str, int]] = {"input": {}, "output": {}}
    for block in blocks:
        direction = block.get("direction")
        if direction not in totals:
            continue
        visual = _BLOCK_VISUAL.get(block.get("block_type"), "other")
        direction_totals = totals[direction]
        direction_totals[visual] = direction_totals.get(visual, 0) + block["token_count"]
    return totals


# Per-request lines an agent injects at the very start of a system prompt. Their values
# (hashes, previous request ids, turn counters) change on every request, so they must not
# be part of block identity. Keep this list explicit: add a pattern deliberately when
# another agent turns out to do the same.
_VOLATILE_HEADER_PATTERNS = (
    # Claude Code: "x-anthropic-billing-header: cc_version=…; cch=…; cc_prev_req=…;"
    re.compile(r"x-anthropic-billing-header:[^\r\n]*(?:\r?\n|$)"),
)


def split_volatile_header(content: str) -> tuple[str, str | None]:
    """Split a leading per-request header line off ``content``.

    Returns ``(stable_text, header)``; ``header`` is None (and the text untouched) when the
    content does not start with a known volatile header. Only a header at the very start is
    removed, so the same text quoted elsewhere in a prompt is left alone.
    """
    for pattern in _VOLATILE_HEADER_PATTERNS:
        match = pattern.match(content)
        if match:
            return content[match.end():], match.group(0)
    return content, None


def content_hash(content: str) -> str | None:
    """sha256 of normalised content; None for empty/hidden content."""
    if not content:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class Block:
    direction: str                      # Direction
    block_type: str                     # BlockType
    content: str                        # normalised text; "" when the provider hides it
    position: int = 0                   # order within its direction, assigned by adapter/pipeline
    message_index: int | None = None    # wire-format message this part came from
    category: str | None = None         # semantic 8-category label; set by classify_blocks (input only)
    content_hash: str | None = None
    token_count: int = 0
    tool_name: str | None = None
    tool_call_id: str | None = None
    attrs: dict = field(default_factory=dict)
    # What produced the block ("tool:Read", "mcp:server/tool", "bash:git", "user", ...); set by
    # analysis/purpose.py:classify_request, persisted as blocks.source_key.
    source_key: str | None = None
    # The file a read/edit tool call targets (and its result): analysis/paths.py, persisted as blocks.file_path.
    file_path: str | None = None
    # Typed path into the canonical request (input blocks) or response (output blocks) JSON
    # document the block derives from, e.g. ("messages", 3, "content", 1). None = no honest location.
    json_path: tuple[str | int, ...] | None = None

    @classmethod
    def make(
        cls,
        direction: str,
        block_type: str,
        content: str,
        *,
        message_index: int | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        attrs: dict | None = None,
        token_count: int | None = None,
        json_path: tuple[str | int, ...] | None = None,
    ) -> "Block":
        """Construct a block, auto-computing content_hash and token_count from content.

        Pass an explicit ``token_count`` for blocks whose content is hidden by
        the provider (e.g. OpenAI reasoning summaries) — content stays "" and
        content_hash stays None, but the provider-reported count is preserved.

        A leading per-request header on a system prompt (see ``split_volatile_header``) is
        moved to ``attrs["volatile_header"]``: ``content`` and ``content_hash`` describe the
        stable text so the block keeps its identity across requests, while ``token_count`` is
        still counted on the full text that was actually sent.
        """
        if block_type == BlockType.SYSTEM_PROMPT and direction == Direction.INPUT:
            stable, header = split_volatile_header(content)
            if header is not None:
                if token_count is None:
                    token_count = count_tokens(content)
                attrs = {**(attrs or {}), "volatile_header": header}
                content = stable
        return cls(
            direction=direction,
            block_type=block_type,
            content=content,
            message_index=message_index,
            content_hash=content_hash(content),
            token_count=token_count if token_count is not None else count_tokens(content),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            attrs=attrs or {},
            json_path=json_path,
        )


class BlockView(Protocol):
    """The read-only block shape the classification code needs.

    ``Block`` satisfies it, and so does ``BlockSnapshot`` (built from stored rows), which
    lets capture and the v9 backfill share one implementation.
    """

    direction: str
    block_type: str
    message_index: int | None
    tool_name: str | None
    tool_call_id: str | None
    attrs: dict
    content: str | None


@dataclass
class BlockSnapshot:
    """A ``BlockView`` rebuilt from a persisted block row (content may be purged = None)."""

    direction: str
    block_type: str
    message_index: int | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    attrs: dict = field(default_factory=dict)
    content: str | None = None
    # A hash exists only for non-empty content, so it tells "has text" when content was not loaded.
    content_hash: str | None = None


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class AnalyzedRequest:
    """Provider-agnostic result of parsing one request/response pair. Replaces ParsedRequest."""

    model: str | None
    input_blocks: list[Block]
    output_blocks: list[Block]
    usage: Usage
    tool_call_map: dict[str, str] = field(default_factory=dict)

    @property
    def response_text(self) -> str:
        """Concatenated assistant text output blocks (excludes thinking/tool calls)."""
        return "\n".join(
            b.content for b in self.output_blocks
            if b.block_type == BlockType.ASSISTANT_MESSAGE and b.content
        )

    @property
    def thinking_text(self) -> str:
        """Concatenated thinking output blocks (empty for hidden/redacted thinking)."""
        return "\n".join(
            b.content for b in self.output_blocks
            if b.block_type == BlockType.THINKING and b.content
        )
