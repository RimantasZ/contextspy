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
from dataclasses import dataclass, field
from enum import StrEnum

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
    ) -> "Block":
        """Construct a block, auto-computing content_hash and token_count from content.

        Pass an explicit ``token_count`` for blocks whose content is hidden by
        the provider (e.g. OpenAI reasoning summaries) — content stays "" and
        content_hash stays None, but the provider-reported count is preserved.
        """
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
        )


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
