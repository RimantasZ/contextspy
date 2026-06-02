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
    _sse_to_openai_responses_resp,
    _wire_format,
    parse_anthropic,
    parse_openai,
    parse_openai_responses,
    parse_request,
    parse_sse_request,
)

try:
    from contextspy.proxy.addon import _detect_agent, _detect_provider
    _HAS_ADDON = True
except ImportError:
    _HAS_ADDON = False


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

OPENAI_RESPONSES_REQ: dict = {
    "model": "gpt-4o",
    "instructions": "You are helpful.",
    "input": [
        {"role": "user", "content": "Say hello"},
    ],
}

OPENAI_RESPONSES_RESP: dict = {
    "id": "resp_01",
    "object": "response",
    "model": "gpt-4o-2024-11-20",
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello world"}],
        }
    ],
    "usage": {"input_tokens": 20, "output_tokens": 42},
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


def _make_openai_responses_sse(
    text: str = "Hello world",
    input_tokens: int = 20,
    output_tokens: int = 42,
) -> bytes:
    """Build a minimal OpenAI Responses API SSE stream."""
    events = [
        {"type": "response.created", "response": {"model": "gpt-4o"}},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "message", "role": "assistant"}},
    ]
    for word in text.split():
        events.append({
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "delta": word + " ",
        })
    events.append({
        "type": "response.completed",
        "response": {
            "model": "gpt-4o",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    })
    lines = ["data: " + json.dumps(e, separators=(",", ":")) for e in events]
    lines.append("data: [DONE]")
    return "\n".join(lines).encode()


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

    def test_openai_responses_api(self):
        assert _wire_format("/v1/responses") == "openai_responses"
        assert _wire_format("/responses") == "openai_responses"

    def test_opencode_zen_anthropic_path(self):
        """opencode zen → Claude uses /zen/v1/messages — must route to anthropic, not responses."""
        assert _wire_format("/zen/v1/messages") == "anthropic"

    def test_opencode_zen_openai_path(self):
        """opencode zen → OpenAI uses /zen/v1/chat/completions — must route to openai."""
        assert _wire_format("/zen/v1/chat/completions") == "openai"

    def test_messages_checked_before_responses(self):
        """/messages takes priority so a hypothetical /responses/messages routes to anthropic."""
        assert _wire_format("/responses/messages") == "anthropic"

    def test_chat_completions_checked_before_responses(self):
        """/chat/completions takes priority over any /responses suffix."""
        assert _wire_format("/v1/chat/completions/responses") == "openai"


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


# ---------------------------------------------------------------------------
# OpenAI Responses API  (/v1/responses)
# ---------------------------------------------------------------------------

class TestOpenAIResponsesApi:
    """Parser tests for the OpenAI Responses API wire format."""

    # --- Non-streaming ---

    def test_parse_basic(self):
        result = parse_request("openai", "/v1/responses", OPENAI_RESPONSES_REQ, OPENAI_RESPONSES_RESP)
        assert result is not None
        assert result.response_text == "Hello world"
        assert result.provider_input_tokens == 20
        assert result.provider_output_tokens == 42

    def test_parse_instructions_become_system_message(self):
        result = parse_request("openai", "/v1/responses", OPENAI_RESPONSES_REQ, OPENAI_RESPONSES_RESP)
        assert result is not None
        system_msgs = [m for m in result.messages if m.role == "system"]
        assert system_msgs, "instructions field must produce a system message"
        assert system_msgs[0].content == "You are helpful."

    def test_parse_user_message_in_input(self):
        result = parse_request("openai", "/v1/responses", OPENAI_RESPONSES_REQ, OPENAI_RESPONSES_RESP)
        assert result is not None
        assert any(m.role == "user" and "hello" in m.content.lower() for m in result.messages)

    def test_parse_tool_definitions(self):
        req = {**OPENAI_RESPONSES_REQ, "tools": [{"type": "function", "function": {"name": "search"}}]}
        result = parse_request("openai", "/v1/responses", req, OPENAI_RESPONSES_RESP)
        assert result is not None
        assert "search" in result.tool_definitions_text

    def test_parse_function_call_in_output(self):
        resp = {
            "model": "gpt-4o",
            "output": [
                {"type": "function_call", "call_id": "c1", "name": "search", "arguments": '{"q":"test"}'},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = parse_request("openai", "/v1/responses", OPENAI_RESPONSES_REQ, resp)
        assert result is not None
        assert result.tool_call_map == {"c1": "search"}
        assert "search" in result.response_text

    def test_parse_function_call_in_input(self):
        """function_call + function_call_output items in input (multi-turn history)."""
        req = {
            "model": "gpt-4o",
            "input": [
                {"role": "user", "content": "What's the weather?"},
                {"type": "function_call", "call_id": "c1", "name": "get_weather",
                 "arguments": '{"city":"NYC"}'},
                {"type": "function_call_output", "call_id": "c1", "output": "Sunny, 72F"},
            ],
        }
        result = parse_request("openai", "/v1/responses", req, OPENAI_RESPONSES_RESP)
        assert result is not None
        assert result.tool_call_map == {"c1": "get_weather"}
        tool_result = next((m for m in result.messages if m.is_tool_result), None)
        assert tool_result is not None
        assert tool_result.content == "Sunny, 72F"

    def test_parse_no_usage(self):
        resp = {"model": "gpt-4o", "output": [], "usage": {}}
        result = parse_openai_responses(OPENAI_RESPONSES_REQ, resp)
        assert result.provider_input_tokens is None
        assert result.provider_output_tokens is None

    def test_parse_empty_input(self):
        result = parse_openai_responses({"model": "gpt-4o", "input": []}, {})
        assert result is not None
        assert result.messages == []

    # --- SSE ---

    def test_sse_accumulates_text(self):
        raw = _make_openai_responses_sse(text="Hello world", input_tokens=20, output_tokens=42)
        resp = _sse_to_openai_responses_resp(raw)
        assert resp["usage"]["input_tokens"] == 20
        assert resp["usage"]["output_tokens"] == 42
        text_item = next(o for o in resp["output"] if o["type"] == "message")
        assert "Hello" in text_item["content"][0]["text"]

    def test_sse_accumulates_function_call(self):
        events = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "call_id": "fc1", "name": "search"}},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"q":'},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '"test"}'},
            {"type": "response.completed",
             "response": {"model": "gpt-4o", "usage": {"input_tokens": 10, "output_tokens": 5}}},
        ]
        raw = b"\n".join(b"data: " + json.dumps(e).encode() for e in events)
        resp = _sse_to_openai_responses_resp(raw)
        fc = next(o for o in resp["output"] if o["type"] == "function_call")
        assert fc["name"] == "search"
        assert fc["call_id"] == "fc1"
        assert fc["arguments"] == '{"q":"test"}'

    def test_sse_empty_stream(self):
        resp = _sse_to_openai_responses_resp(b"")
        assert resp["output"] == []
        assert "usage" not in resp

    def test_parse_sse_request_routing(self):
        raw = _make_openai_responses_sse(text="Hi", input_tokens=10, output_tokens=3)
        result = parse_sse_request("openai", "/v1/responses", OPENAI_RESPONSES_REQ, raw)
        assert result is not None
        assert "Hi" in result.response_text
        assert result.provider_input_tokens == 10
        assert result.provider_output_tokens == 3

    def test_opencode_zen_responses_path(self):
        """opencode routing an OpenAI model via zen gateway: /zen/v1/responses still parsed."""
        raw = _make_openai_responses_sse(text="Hi", input_tokens=10, output_tokens=3)
        result = parse_sse_request("opencode_zen", "/zen/v1/responses", OPENAI_RESPONSES_REQ, raw)
        assert result is not None
        assert result.provider_output_tokens == 3


# ---------------------------------------------------------------------------
# Provider and agent detection  (addon routing)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestProviderDetection:
    """Guards against accidentally breaking host→provider routing when adding new entries.

    Each test documents one live integration and the provider label it must produce.
    """

    def test_claude_code(self):
        """Claude Code talks directly to api.anthropic.com."""
        assert _detect_provider("api.anthropic.com", 443) == "anthropic"

    def test_anthropic_subdomain(self):
        assert _detect_provider("bedrock.api.anthropic.com", 443) == "anthropic"

    def test_openai_direct(self):
        assert _detect_provider("api.openai.com", 443) == "openai"

    def test_azure_openai(self):
        assert _detect_provider("myinstance.openai.azure.com", 443) == "openai_azure"

    def test_copilot_legacy_proxy(self):
        assert _detect_provider("copilot-proxy.githubusercontent.com", 443) == "copilot"

    def test_copilot_api_subdomain(self):
        """api.githubcopilot.com must match via endswith(.githubcopilot.com)."""
        assert _detect_provider("api.githubcopilot.com", 443) == "copilot"

    def test_opencode_zen_gateway(self):
        """opencode.ai zen gateway — added to support opencode cloud model routing."""
        assert _detect_provider("opencode.ai", 443) == "opencode_zen"

    def test_opencode_zen_subdomain(self):
        assert _detect_provider("api.opencode.ai", 443) == "opencode_zen"

    def test_ollama_port(self):
        """Ollama is detected by port (11434), not host — works for any bind address."""
        assert _detect_provider("localhost", 11434) == "ollama"
        assert _detect_provider("127.0.0.1", 11434) == "ollama"

    def test_unknown_host_returns_none(self):
        """Unrecognised hosts must return None so the addon skips them."""
        assert _detect_provider("example.com", 443) is None

    def test_telemetry_hosts_return_none(self):
        """Hosts seen in real traffic that must NOT be treated as LLM providers."""
        assert _detect_provider("eu-central-1-1.aws.cloud2.influxdata.com", 443) is None
        assert _detect_provider("models.dev", 443) is None


@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestAgentDetection:
    """Guards against breaking User-Agent → agent label mapping."""

    def test_claude_code_sdk(self):
        assert _detect_agent("anthropic-python/0.50.0 Python/3.12") == "claude_sdk"

    def test_github_copilot(self):
        assert _detect_agent("GitHubCopilot/1.0 vscode/1.89") == "github_copilot"
        assert _detect_agent("github-copilot-chat/0.14") == "github_copilot"

    def test_openai_sdk(self):
        assert _detect_agent("openai-python/1.30.0 Python/3.11") == "openai_sdk"

    def test_opencode(self):
        assert _detect_agent("opencode/0.1.100") == "opencode"

    def test_cursor(self):
        assert _detect_agent("cursor/0.42.0") == "cursor"

    def test_unknown(self):
        assert _detect_agent("curl/7.88.1") == "unknown"
        assert _detect_agent("") == "unknown"
