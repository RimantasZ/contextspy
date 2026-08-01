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
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from contextspy.analysis.blocks import Block, BlockType, Direction, Usage


class WireFormatAdapter(ABC):
    """One provider wire format: request/response JSON <-> Block/Usage.

    ``endpoint_patterns`` are substrings matched against the request path —
    dispatch is endpoint-based (not host-based) so a gateway that relays one
    provider's traffic in another provider's wire format (e.g. Copilot -> Claude,
    opencode's zen gateway) is still parsed correctly.
    """

    format_id: str
    endpoint_patterns: tuple[str, ...]

    @abstractmethod
    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        """Return (input_blocks, tool_call_map) for the request body."""

    @abstractmethod
    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        """Return (output_blocks, usage) for a buffered (non-streaming) response body."""

    @abstractmethod
    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        """Return (output_blocks, usage) reconstructed from a raw streaming response."""

    def parse_events(self, events: list[dict]) -> tuple[list[Block], Usage]:
        """Return (output_blocks, usage) from already-decoded events (no SSE framing).

        Used by transports that deliver discrete event objects directly (e.g.
        WebSocket text frames) rather than an SSE byte stream. Not abstract —
        adapters without an event-level parser (Anthropic, OpenAI Chat, Ollama)
        don't need a stub; callers should catch NotImplementedError and degrade
        gracefully (e.g. the WS addon path).
        """
        raise NotImplementedError(f"{self.format_id} has no event-level parser")


REGISTRY: list[WireFormatAdapter] = []


def register(adapter: WireFormatAdapter) -> None:
    REGISTRY.append(adapter)


def get_adapter(endpoint: str) -> WireFormatAdapter | None:
    for adapter in REGISTRY:
        if any(pattern in endpoint for pattern in adapter.endpoint_patterns):
            return adapter
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def extract_sse_events(raw: bytes) -> list[dict]:
    """Decode an SSE byte stream's ``data: `` lines into a list of event dicts.

    Tolerant of blank lines, ``[DONE]`` sentinels, and malformed JSON (skipped).
    """
    text_data = raw.decode("utf-8", errors="replace")
    events: list[dict] = []
    for line in text_data.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def reconcile_thinking(blocks: list[Block], usage: Usage) -> None:
    """Give a response's thinking a token count, whatever the provider disclosed.

    Providers expose reasoning in three mutually exclusive ways, and every one
    of them has to land on the same two carriers: ``BlockType.THINKING`` blocks
    for the text, ``Usage.reasoning_tokens`` for the provider's own figure.
    Called by every adapter at the end of ``parse_response``/``parse_sse`` so
    the rules live in one place rather than four.

    Each thinking block comes out tagged with ``attrs["token_source"]``,
    recording how much its ``token_count`` can be trusted:

    ``provider``
        The API reported a reasoning-token count for the turn (OpenAI's
        ``completion_tokens_details``/``output_tokens_details.reasoning_tokens``).
        Authoritative — it is what was billed — and it is used even when the
        text is withheld, or when the visible text is only a short summary of
        much longer hidden reasoning.
    ``estimated``
        No count was reported but the text came back (Anthropic
        ``thinking.display: "summarized"``, Ollama ``thinking``, DeepSeek/vLLM
        ``reasoning_content``). The tokenizer estimate on the text stands.
    ``derived``
        Neither a count nor any text — the provider says only "thinking
        happened here" (Anthropic ``thinking.display: "omitted"``, the default
        on current Claude models, and ``redacted_thinking``). Anthropic bills
        thinking inside ``output_tokens`` but never breaks it out, so the
        residual left after subtracting the visible output is the only signal
        available. Note the visible side is a tiktoken estimate against a
        different tokenizer, so error there lands wholly on this figure.

    Blocks whose text the provider withheld are also tagged ``hidden``.
    ``usage.reasoning_tokens`` is deliberately left alone: it means "what the
    provider reported" and nothing else, which is what makes the reported-vs-
    estimated comparison in the UI meaningful.
    """
    thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
    reported = usage.reasoning_tokens

    if reported:
        if not thinking:
            # Billed for reasoning that never appeared in the output in any form.
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.THINKING, "",
                attrs={"hidden": True, "token_source": "provider"},
                token_count=reported,
            ))
            return
        _spread_tokens(thinking, reported)
        source = "provider"
    elif not thinking:
        return
    elif any(b.content for b in thinking):
        source = "estimated"
    else:
        derived = _residual_output_tokens(blocks, usage)
        if derived is not None:
            thinking[0].token_count = derived
        source = "derived" if derived is not None else "unknown"

    for b in thinking:
        b.attrs["token_source"] = source
        if not b.content:
            b.attrs["hidden"] = True


def _spread_tokens(blocks: list[Block], total: int) -> None:
    """Apportion one provider-reported total across n thinking blocks.

    Split proportionally to what each block's text already estimated to, so
    per-block numbers stay meaningful, with the remainder on the last block so
    the parts always re-add to ``total``.
    """
    if len(blocks) == 1:
        blocks[0].token_count = total
        return
    weights = [b.token_count for b in blocks]
    weight_total = sum(weights)
    if not weight_total:
        for b in blocks:
            b.token_count = 0
        blocks[0].token_count = total
        return
    running = 0
    for b, weight in zip(blocks[:-1], weights[:-1]):
        b.token_count = total * weight // weight_total
        running += b.token_count
    blocks[-1].token_count = total - running


def _residual_output_tokens(blocks: list[Block], usage: Usage) -> int | None:
    """Output tokens the visible (non-thinking) response does not account for."""
    if usage.output_tokens is None:
        return None
    visible = sum(
        b.token_count for b in blocks
        if b.direction == Direction.OUTPUT and b.block_type != BlockType.THINKING
    )
    residual = usage.output_tokens - visible
    return residual if residual > 0 else None


def flatten_content(content: Any) -> str:
    """Flatten a provider content value (str, or list of content-part dicts) to text.

    Used for content that stays a single block even though it may itself be
    a nested list — e.g. a tool_result's inner content array, or a plain
    multimodal message with no text/tool parts worth splitting out.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") in ("output_text", "input_text"):
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(flatten_content(block.get("content", "")))
                elif "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return json.dumps(content) if content is not None else ""
