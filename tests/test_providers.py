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
"""Tests for contextspy.analysis.providers.

Covers every wire-format / provider combination including the Copilot-via-Claude
case that was broken (provider="copilot", endpoint="/v1/messages", Anthropic SSE).
"""
from __future__ import annotations

import json
import textwrap

import pytest

from contextspy.analysis.providers import (
    ParsedRequest,
    _sse_to_anthropic_resp,
    _sse_to_openai_resp,
    _wire_format,
    parse_anthropic,
    parse_openai,
    parse_request,
    parse_sse_request,
)


# ---------------------------------------------------------------------------
# Fixtures — request bodies
# ---------------------------------------------------------------------------

ANTHROPIC_REQ: dict = {
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Say hello"}],
    "system": "You are helpful.",
    "max_tokens": 256,
}

OPENAI_REQ: dict = {
    "model": "gpt-4o",
    "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Say hello"},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures — non-streaming response bodies
# ---------------------------------------------------------------------------

ANTHROPIC_RESP: dict = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "Hello world"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 42,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 100,
    },
}

OPENAI_RESP: dict = {
    "id": "chatcmpl-01",
    "object": "chat.completion",
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello world"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 42},
}

OLLAMA_RESP: dict = {
    "model": "llama3",
    "message": {"role": "assistant", "content": "Hello world"},
    "done": True,
    "prompt_eval_count": 20,
    "eval_count": 42,
}


# ---------------------------------------------------------------------------
# Fixtures — SSE response bytes
# ---------------------------------------------------------------------------

def _make_anthropic_sse(
    text: str = "Hello world",
    input_tokens: int = 10,
    output_tokens: int = 42,
    cache_read: int = 500,
    cache_creation: int = 100,
) -> bytes:
    lines = [
        "event: message_start",
        json.dumps({
            "type": "message_start",
            "message": {
                "id": "msg_01",
                "model": "claude-sonnet-4-6",
                "role": "assistant",
                "type": "message",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 1,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                },
            },
        }, separators=(",", ":")),
        "",
        "event: content_block_start",
        json.dumps({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}},
                   separators=(",", ":")),
        "",
    ]
    # Emit one delta per word so we exercise the accumulation loop
    for word in text.split():
        lines += [
            "event: content_block_delta",
            json.dumps({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": word + " "}},
                       separators=(",", ":")),
            "",
        ]
    lines += [
        "event: content_block_stop",
        json.dumps({"type": "content_block_stop", "index": 0}, separators=(",", ":")),
        "",
        "event: message_delta",
        json.dumps({"type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": output_tokens}},
                   separators=(",", ":")),
        "",
        "event: message_stop",
        json.dumps({"type": "message_stop"}, separators=(",", ":")),
        "",
        "data: [DONE]",
        "",
    ]
    # SSE format: each line is either "event: …" or "data: …"
    sse_lines: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("event:"):
            sse_lines.append(line)
        elif line == "":
            sse_lines.append("")
        elif line == "data: [DONE]":
            sse_lines.append(line)
        else:
            sse_lines.append("data: " + line)
    return "\n".join(sse_lines).encode()


def _make_copilot_claude_sse(
    text: str = "Hello world",
    input_tokens: int = 1,
    output_tokens: int = 42,
    cache_read: int = 40876,
    cache_creation: int = 3926,
) -> bytes:
    """Anthropic SSE as returned by Copilot/Bedrock: all token counts in message_delta."""
    lines = [
        "event: message_start",
        json.dumps({
            "type": "message_start",
            "message": {
                "id": "msg_bdrk_01",
                "model": "claude-sonnet-4-6",
                "role": "assistant",
                "type": "message",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }, separators=(",", ":")),
        "",
        "event: content_block_start",
        json.dumps({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}},
                   separators=(",", ":")),
        "",
    ]
    for word in text.split():
        lines += [
            "event: content_block_delta",
            json.dumps({"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": word + " "}},
                       separators=(",", ":")),
            "",
        ]
    lines += [
        "event: content_block_stop",
        json.dumps({"type": "content_block_stop", "index": 0}, separators=(",", ":")),
        "",
        "event: message_delta",
        # Copilot/Bedrock: all four token counts in message_delta
        json.dumps({
            "type": "message_delta",
            "copilot_usage": {"total_nano_aiu": 0},
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        }, separators=(",", ":")),
        "",
        "event: message_stop",
        json.dumps({"type": "message_stop"}, separators=(",", ":")),
        "",
        "data: [DONE]",
        "",
    ]
    sse_lines: list[str] = []
    for line in lines:
        if line.startswith("event:"):
            sse_lines.append(line)
        elif line == "":
            sse_lines.append("")
        elif line == "data: [DONE]":
            sse_lines.append(line)
        else:
            sse_lines.append("data: " + line)
    return "\n".join(sse_lines).encode()


def _make_openai_sse(
    text: str = "Hello world",
    prompt_tokens: int = 20,
    completion_tokens: int = 42,
) -> bytes:
    chunks: list[str] = []
    for i, word in enumerate(text.split()):
        chunks.append("data: " + json.dumps({
            "id": "chatcmpl-01",
            "object": "chat.completion.chunk",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"content": word + (" " if i < len(text.split()) - 1 else "")}}],
        }, separators=(",", ":")))
    chunks.append("data: " + json.dumps({
        "id": "chatcmpl-01",
        "object": "chat.completion.chunk",
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }, separators=(",", ":")))
    chunks.append("data: [DONE]")
    return "\n".join(chunks).encode()


# ---------------------------------------------------------------------------
# _wire_format
# ---------------------------------------------------------------------------

class TestWireFormat:
    def test_anthropic_messages(self):
        assert _wire_format("/v1/messages") == "anthropic"
        assert _wire_format("/messages") == "anthropic"

    def test_openai_chat_completions(self):
        assert _wire_format("/v1/chat/completions") == "openai"
        assert _wire_format("/chat/completions") == "openai"

    def test_openai_completions(self):
        assert _wire_format("/v1/completions") == "openai"
        assert _wire_format("/completions") == "openai"

    def test_ollama_native(self):
        assert _wire_format("/api/chat") == "ollama_native"
        assert _wire_format("/api/generate") == "ollama_native"

    def test_unknown_returns_none(self):
        assert _wire_format("/telemetry") is None
        assert _wire_format("/health") is None
        assert _wire_format("/") is None
        assert _wire_format("") is None


# ---------------------------------------------------------------------------
# _sse_to_anthropic_resp — low-level SSE parsing
# ---------------------------------------------------------------------------

class TestSseToAnthropicResp:
    def test_standard_stream(self):
        raw = _make_anthropic_sse(text="Hello world", input_tokens=10,
                                   output_tokens=42, cache_read=500, cache_creation=100)
        resp = _sse_to_anthropic_resp(raw)
        assert resp["usage"]["input_tokens"] == 10
        assert resp["usage"]["output_tokens"] == 42
        assert resp["usage"]["cache_read_input_tokens"] == 500
        assert resp["usage"]["cache_creation_input_tokens"] == 100
        text = resp["content"][0]["text"]
        assert "Hello" in text and "world" in text

    def test_copilot_bedrock_stream_all_tokens_in_message_delta(self):
        """Copilot/Bedrock sends all token counts in message_delta, not just output_tokens."""
        raw = _make_copilot_claude_sse(
            text="Hi there",
            input_tokens=1,
            output_tokens=220,
            cache_read=40876,
            cache_creation=3926,
        )
        resp = _sse_to_anthropic_resp(raw)
        assert resp["usage"]["output_tokens"] == 220
        # cache tokens must be captured (from message_start in this fixture)
        assert resp["usage"]["cache_read_input_tokens"] == 40876
        assert resp["usage"]["cache_creation_input_tokens"] == 3926

    def test_empty_stream(self):
        resp = _sse_to_anthropic_resp(b"")
        assert resp["content"][0]["text"] == ""


# ---------------------------------------------------------------------------
# parse_sse_request — the key regression test
# ---------------------------------------------------------------------------

class TestParseSseRequest:
    # --- Copilot + Claude (the previously broken case) ---

    def test_copilot_claude_sse_returns_parsed_request(self):
        """provider=copilot, endpoint=/v1/messages must use the Anthropic parser."""
        raw = _make_copilot_claude_sse(text="Hello world", input_tokens=1,
                                        output_tokens=220, cache_read=40876,
                                        cache_creation=3926)
        result = parse_sse_request("copilot", "/v1/messages", ANTHROPIC_REQ, raw)
        assert result is not None, "Must not return None for Copilot+Claude streaming"
        assert "Hello" in result.response_text
        assert result.provider_output_tokens == 220
        # total input = billed + cache_read + cache_creation
        assert result.provider_input_tokens == 1 + 40876 + 3926

    def test_copilot_claude_sse_nonzero_tokens(self):
        """Regression: tokens_in and tokens_out must be > 0 for Copilot+Claude."""
        raw = _make_copilot_claude_sse(text="Test", output_tokens=5,
                                        input_tokens=2, cache_read=100, cache_creation=50)
        result = parse_sse_request("copilot", "/v1/messages", ANTHROPIC_REQ, raw)
        assert result is not None
        assert result.provider_input_tokens and result.provider_input_tokens > 0
        assert result.provider_output_tokens and result.provider_output_tokens > 0

    # --- Direct Anthropic streaming ---

    def test_anthropic_sse(self):
        raw = _make_anthropic_sse(text="Hello world", input_tokens=10, output_tokens=42,
                                   cache_read=500, cache_creation=100)
        result = parse_sse_request("anthropic", "/v1/messages", ANTHROPIC_REQ, raw)
        assert result is not None
        assert result.provider_input_tokens == 610  # 10 + 500 + 100
        assert result.provider_output_tokens == 42
        assert "Hello" in result.response_text

    # --- OpenAI streaming ---

    def test_openai_sse(self):
        raw = _make_openai_sse(text="Hello world", prompt_tokens=20, completion_tokens=42)
        result = parse_sse_request("openai", "/v1/chat/completions", OPENAI_REQ, raw)
        assert result is not None
        assert result.provider_input_tokens == 20
        assert result.provider_output_tokens == 42
        assert "Hello" in result.response_text

    def test_copilot_openai_sse(self):
        """Copilot using OpenAI-format backend (/chat/completions) still works."""
        raw = _make_openai_sse(text="Hello world", prompt_tokens=20, completion_tokens=42)
        result = parse_sse_request("copilot", "/v1/chat/completions", OPENAI_REQ, raw)
        assert result is not None
        assert result.provider_output_tokens == 42

    def test_openai_azure_sse(self):
        raw = _make_openai_sse(text="Hi", prompt_tokens=5, completion_tokens=3)
        result = parse_sse_request("openai_azure", "/chat/completions", OPENAI_REQ, raw)
        assert result is not None
        assert result.provider_output_tokens == 3

    # --- Unknown endpoint ---

    def test_unknown_endpoint_returns_none(self):
        result = parse_sse_request("copilot", "/telemetry", {}, b"data: {}\n")
        assert result is None

    def test_empty_sse_bytes(self):
        result = parse_sse_request("anthropic", "/v1/messages", ANTHROPIC_REQ, b"")
        assert result is not None  # returns a ParsedRequest with empty/zero fields


# ---------------------------------------------------------------------------
# parse_request — non-streaming
# ---------------------------------------------------------------------------

class TestParseRequest:
    def test_anthropic_direct(self):
        result = parse_request("anthropic", "/v1/messages", ANTHROPIC_REQ, ANTHROPIC_RESP)
        assert result is not None
        assert result.response_text == "Hello world"
        assert result.provider_output_tokens == 42
        assert result.provider_input_tokens == 610  # 10 + 500 + 100
        assert result.cache_read_tokens == 500
        assert result.cache_creation_tokens == 100

    def test_copilot_via_anthropic_endpoint(self):
        """Copilot relaying to Claude (non-streaming) must use the Anthropic parser."""
        result = parse_request("copilot", "/v1/messages", ANTHROPIC_REQ, ANTHROPIC_RESP)
        assert result is not None
        assert result.response_text == "Hello world"
        assert result.provider_output_tokens == 42

    def test_openai_direct(self):
        result = parse_request("openai", "/v1/chat/completions", OPENAI_REQ, OPENAI_RESP)
        assert result is not None
        assert result.response_text == "Hello world"
        assert result.provider_input_tokens == 20
        assert result.provider_output_tokens == 42

    def test_copilot_openai_format(self):
        result = parse_request("copilot", "/v1/chat/completions", OPENAI_REQ, OPENAI_RESP)
        assert result is not None
        assert result.provider_output_tokens == 42

    def test_openai_azure(self):
        result = parse_request("openai_azure", "/chat/completions", OPENAI_REQ, OPENAI_RESP)
        assert result is not None
        assert result.provider_output_tokens == 42

    def test_ollama_native(self):
        result = parse_request("ollama", "/api/chat", OPENAI_REQ, OLLAMA_RESP)
        assert result is not None
        assert result.response_text == "Hello world"
        assert result.provider_input_tokens == 20
        assert result.provider_output_tokens == 42

    def test_ollama_openai_compat(self):
        result = parse_request("ollama", "/v1/chat/completions", OPENAI_REQ, OPENAI_RESP)
        assert result is not None
        assert result.provider_output_tokens == 42

    def test_unknown_endpoint_returns_none(self):
        result = parse_request("copilot", "/telemetry", {}, {})
        assert result is None

    def test_malformed_body_returns_none(self):
        """parse_request must not raise on unexpected body shapes."""
        result = parse_request("anthropic", "/v1/messages", {}, {"unexpected": True})
        assert result is not None  # parse_anthropic returns zeroes, not an exception


# ---------------------------------------------------------------------------
# Message content parsing
# ---------------------------------------------------------------------------

class TestMessageParsing:
    def test_system_message_injected_for_anthropic(self):
        result = parse_request("anthropic", "/v1/messages", ANTHROPIC_REQ, ANTHROPIC_RESP)
        assert result is not None
        roles = [m.role for m in result.messages]
        assert "system" in roles

    def test_openai_system_message(self):
        result = parse_request("openai", "/v1/chat/completions", OPENAI_REQ, OPENAI_RESP)
        assert result is not None
        roles = [m.role for m in result.messages]
        assert "system" in roles

    def test_anthropic_cache_tokens_breakdown(self):
        result = parse_request("anthropic", "/v1/messages", ANTHROPIC_REQ, ANTHROPIC_RESP)
        assert result is not None
        assert result.cache_read_tokens == 500
        assert result.cache_creation_tokens == 100

    def test_copilot_claude_cache_tokens(self):
        raw = _make_copilot_claude_sse(cache_read=40876, cache_creation=3926,
                                        input_tokens=1, output_tokens=220, text="Hi")
        result = parse_sse_request("copilot", "/v1/messages", ANTHROPIC_REQ, raw)
        assert result is not None
        assert result.cache_read_tokens == 40876
        assert result.cache_creation_tokens == 3926
