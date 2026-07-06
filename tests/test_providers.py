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
"""Tests for contextspy.analysis.adapters + classifier.

Covers every wire-format / provider combination including the Copilot-via-Claude
case that was broken (endpoint="/v1/messages", Anthropic SSE, regardless of
which host was detected) plus the block-level model introduced in the
blocks/adapters refactor: per-content-part splitting, category assignment,
content-addressed dedup, retention GC, and hidden-reasoning synthesis.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.adapters.anthropic import AnthropicAdapter
from contextspy.analysis.adapters.ollama import OllamaAdapter
from contextspy.analysis.adapters.openai_chat import OpenAIChatAdapter
from contextspy.analysis.adapters.openai_responses import OpenAIResponsesAdapter
from contextspy.analysis.blocks import AnalyzedRequest, BlockType, Direction, Usage
from contextspy.analysis.classifier import classify, classify_blocks, per_tool_tokens

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
    words = text.split()
    for i, word in enumerate(words):
        chunks.append("data: " + json.dumps({
            "id": "chatcmpl-01",
            "object": "chat.completion.chunk",
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"content": word + (" " if i < len(words) - 1 else "")}}],
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
# get_adapter dispatch (was _wire_format)
# ---------------------------------------------------------------------------

class TestGetAdapter:
    def test_anthropic_messages(self):
        assert get_adapter("/v1/messages").format_id == "anthropic"
        assert get_adapter("/messages").format_id == "anthropic"

    def test_openai_chat_completions(self):
        assert get_adapter("/v1/chat/completions").format_id == "openai_chat"
        assert get_adapter("/chat/completions").format_id == "openai_chat"

    def test_openai_completions(self):
        assert get_adapter("/v1/completions").format_id == "openai_chat"
        assert get_adapter("/completions").format_id == "openai_chat"

    def test_ollama_native(self):
        assert get_adapter("/api/chat").format_id == "ollama"
        assert get_adapter("/api/generate").format_id == "ollama"

    def test_unknown_returns_none(self):
        assert get_adapter("/telemetry") is None
        assert get_adapter("/health") is None
        assert get_adapter("/") is None
        assert get_adapter("") is None

    def test_openai_responses_api(self):
        assert get_adapter("/v1/responses").format_id == "openai_responses"
        assert get_adapter("/responses").format_id == "openai_responses"

    def test_opencode_zen_anthropic_path(self):
        assert get_adapter("/zen/v1/messages").format_id == "anthropic"

    def test_opencode_zen_openai_path(self):
        assert get_adapter("/zen/v1/chat/completions").format_id == "openai_chat"

    def test_messages_checked_before_responses(self):
        assert get_adapter("/responses/messages").format_id == "anthropic"

    def test_chat_completions_checked_before_responses(self):
        assert get_adapter("/v1/chat/completions/responses").format_id == "openai_chat"


# ---------------------------------------------------------------------------
# Anthropic adapter — non-streaming
# ---------------------------------------------------------------------------

class TestAnthropicAdapter:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_request_system_and_user(self):
        blocks, tool_call_map = self.adapter.parse_request(ANTHROPIC_REQ)
        types = [b.block_type for b in blocks]
        assert BlockType.SYSTEM_PROMPT in types
        assert BlockType.USER_MESSAGE in types
        assert tool_call_map == {}

    def test_response_text_and_usage(self):
        blocks, usage = self.adapter.parse_response(ANTHROPIC_RESP)
        text_blocks = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert text_blocks and text_blocks[0].content == "Hello world"
        assert usage.output_tokens == 42
        assert usage.input_tokens == 610  # 10 + 500 + 100
        assert usage.cache_read_tokens == 500
        assert usage.cache_creation_tokens == 100

    def test_malformed_body_does_not_raise(self):
        blocks, usage = self.adapter.parse_response({"unexpected": True})
        assert blocks == []
        assert usage.input_tokens is None


# ---------------------------------------------------------------------------
# Anthropic adapter — SSE
# ---------------------------------------------------------------------------

class TestAnthropicSse:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_standard_stream(self):
        raw = _make_anthropic_sse(text="Hello world", input_tokens=10,
                                   output_tokens=42, cache_read=500, cache_creation=100)
        blocks, usage = self.adapter.parse_sse(raw)
        assert usage.input_tokens == 610
        assert usage.output_tokens == 42
        assert usage.cache_read_tokens == 500
        assert usage.cache_creation_tokens == 100
        text = "".join(b.content for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert "Hello" in text and "world" in text

    def test_copilot_bedrock_stream_all_tokens_in_message_delta(self):
        raw = _make_copilot_claude_sse(text="Hi there", input_tokens=1, output_tokens=220,
                                        cache_read=40876, cache_creation=3926)
        blocks, usage = self.adapter.parse_sse(raw)
        assert usage.output_tokens == 220
        assert usage.cache_read_tokens == 40876
        assert usage.cache_creation_tokens == 3926

    def test_empty_stream(self):
        blocks, usage = self.adapter.parse_sse(b"")
        assert blocks == []

    def test_copilot_claude_sse_via_get_adapter(self):
        """endpoint=/v1/messages must dispatch to the Anthropic adapter regardless of host."""
        raw = _make_copilot_claude_sse(text="Hello world", input_tokens=1,
                                        output_tokens=220, cache_read=40876, cache_creation=3926)
        adapter = get_adapter("/v1/messages")
        assert adapter is not None
        blocks, usage = adapter.parse_sse(raw)
        text = "".join(b.content for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert "Hello" in text
        assert usage.output_tokens == 220
        assert usage.input_tokens == 1 + 40876 + 3926


# ---------------------------------------------------------------------------
# Anthropic adapter — thinking, redacted_thinking, cache_control
# ---------------------------------------------------------------------------

class TestAnthropicThinking:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_request_thinking_block(self):
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Solve this"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "Let me think...", "signature": "sig123"},
                    {"type": "text", "text": "The answer is 42."},
                ]},
            ],
        }
        blocks, _ = self.adapter.parse_request(req)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert len(thinking) == 1
        assert thinking[0].content == "Let me think..."
        assert thinking[0].attrs.get("signature") == "sig123"

    def test_request_redacted_thinking_block(self):
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Solve this"},
                {"role": "assistant", "content": [
                    {"type": "redacted_thinking", "data": "encrypted-blob"},
                    {"type": "text", "text": "Done."},
                ]},
            ],
        }
        blocks, _ = self.adapter.parse_request(req)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert len(thinking) == 1
        assert thinking[0].content == ""
        assert thinking[0].attrs.get("redacted") is True

    def test_response_thinking_block(self):
        resp = {
            "content": [
                {"type": "thinking", "thinking": "reasoning...", "signature": "sig"},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == "reasoning..."

    def test_sse_thinking_delta(self):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "step 1 "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "step 2"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "final answer"}},
            {"type": "message_delta", "usage": {"output_tokens": 15}},
        ]
        raw = b"\n".join(b"data: " + json.dumps(e).encode() for e in events)
        blocks, usage = self.adapter.parse_sse(raw)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        text = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert thinking and thinking[0].content == "step 1 step 2"
        assert thinking[0].attrs.get("signature") == "abc"
        assert text and text[0].content == "final answer"

    def test_cache_control_captured(self):
        req = {
            "model": "claude-sonnet-4-6",
            "system": "You are helpful.",
            "tools": [{"name": "search", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
            ]}],
        }
        blocks, _ = self.adapter.parse_request(req)
        tool_block = next(b for b in blocks if b.block_type == BlockType.TOOL_DEFINITION)
        user_block = next(b for b in blocks if b.block_type == BlockType.USER_MESSAGE)
        assert tool_block.attrs.get("cache_control") == {"type": "ephemeral"}
        assert user_block.attrs.get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Anthropic adapter — assistant prefill
# ---------------------------------------------------------------------------

class TestAssistantPrefill:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_trailing_assistant_message_flagged(self):
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Continue the story"},
                {"role": "assistant", "content": "Once upon a time"},
            ],
        }
        blocks, _ = self.adapter.parse_request(req)
        assistant_block = next(b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert assistant_block.attrs.get("is_prefill") is True

    def test_non_trailing_assistant_message_not_flagged(self):
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ],
        }
        blocks, _ = self.adapter.parse_request(req)
        assistant_block = next(b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert not assistant_block.attrs.get("is_prefill")


# ---------------------------------------------------------------------------
# Anthropic adapter — per-content-part block splitting + tool_call_map
# ---------------------------------------------------------------------------

class TestBlockSplitting:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_multiple_tool_results_become_separate_blocks(self):
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "run two tools"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "a", "name": "Read", "input": {"path": "x"}},
                    {"type": "tool_use", "id": "b", "name": "Bash", "input": {"cmd": "ls"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": "file contents"},
                    {"type": "tool_result", "tool_use_id": "b", "content": "dir listing"},
                    {"type": "text", "text": "continue please"},
                ]},
            ],
        }
        blocks, tool_call_map = self.adapter.parse_request(req)
        assert tool_call_map == {"a": "Read", "b": "Bash"}

        results = [b for b in blocks if b.block_type == BlockType.TOOL_RESULT]
        assert len(results) == 2
        assert {r.tool_name for r in results} == {"Read", "Bash"}
        # all three parts of the last message share the same message_index
        last_msg_blocks = [b for b in blocks if b.message_index == 2]
        assert len(last_msg_blocks) == 3
        assert any(b.block_type == BlockType.USER_MESSAGE and b.content == "continue please"
                   for b in last_msg_blocks)


# ---------------------------------------------------------------------------
# OpenAI Chat Completions adapter
# ---------------------------------------------------------------------------

class TestOpenAIChatAdapter:
    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

    def test_request_system_message(self):
        blocks, _ = self.adapter.parse_request(OPENAI_REQ)
        assert any(b.block_type == BlockType.SYSTEM_PROMPT for b in blocks)

    def test_response(self):
        blocks, usage = self.adapter.parse_response(OPENAI_RESP)
        text_blocks = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert text_blocks[0].content == "Hello world"
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42

    def test_sse(self):
        raw = _make_openai_sse(text="Hello world", prompt_tokens=20, completion_tokens=42)
        blocks, usage = self.adapter.parse_sse(raw)
        text = "".join(b.content for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert "Hello" in text
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42

    def test_tool_call_round_trip(self):
        req = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "what's the weather"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "get_weather", "arguments": '{"city":"NYC"}'}},
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": "Sunny, 72F"},
            ],
        }
        blocks, tool_call_map = self.adapter.parse_request(req)
        assert tool_call_map == {"call_1": "get_weather"}
        result = next(b for b in blocks if b.block_type == BlockType.TOOL_RESULT)
        assert result.tool_name == "get_weather"
        assert result.content == "Sunny, 72F"

    def test_malformed_body_does_not_raise(self):
        blocks, tool_call_map = self.adapter.parse_request({})
        assert blocks == []
        assert tool_call_map == {}


# ---------------------------------------------------------------------------
# OpenAI Responses API adapter
# ---------------------------------------------------------------------------

class TestOpenAIResponsesAdapter:
    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

    def test_instructions_become_system_block(self):
        blocks, _ = self.adapter.parse_request(OPENAI_RESPONSES_REQ)
        system_blocks = [b for b in blocks if b.block_type == BlockType.SYSTEM_PROMPT]
        assert system_blocks and system_blocks[0].content == "You are helpful."

    def test_user_message_in_input(self):
        blocks, _ = self.adapter.parse_request(OPENAI_RESPONSES_REQ)
        assert any(b.block_type == BlockType.USER_MESSAGE and "hello" in b.content.lower() for b in blocks)

    def test_tool_definitions(self):
        req = {**OPENAI_RESPONSES_REQ, "tools": [{"type": "function", "function": {"name": "search"}}]}
        blocks, _ = self.adapter.parse_request(req)
        assert any(b.block_type == BlockType.TOOL_DEFINITION for b in blocks)

    def test_function_call_in_output(self):
        resp = {
            "model": "gpt-4o",
            "output": [
                {"type": "function_call", "call_id": "c1", "name": "search", "arguments": '{"q":"test"}'},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        blocks, usage = self.adapter.parse_response(resp)
        call = next(b for b in blocks if b.block_type == BlockType.TOOL_CALL)
        assert call.tool_name == "search"
        assert call.tool_call_id == "c1"

    def test_function_call_in_input_history(self):
        req = {
            "model": "gpt-4o",
            "input": [
                {"role": "user", "content": "What's the weather?"},
                {"type": "function_call", "call_id": "c1", "name": "get_weather", "arguments": '{"city":"NYC"}'},
                {"type": "function_call_output", "call_id": "c1", "output": "Sunny, 72F"},
            ],
        }
        blocks, tool_call_map = self.adapter.parse_request(req)
        assert tool_call_map == {"c1": "get_weather"}
        result = next(b for b in blocks if b.block_type == BlockType.TOOL_RESULT)
        assert result.tool_name == "get_weather"
        assert result.content == "Sunny, 72F"

    def test_no_usage(self):
        blocks, usage = self.adapter.parse_response({"model": "gpt-4o", "output": [], "usage": {}})
        assert usage.input_tokens is None
        assert usage.output_tokens is None

    def test_empty_input(self):
        blocks, _ = self.adapter.parse_request({"model": "gpt-4o", "input": []})
        assert blocks == []

    def test_sse_accumulates_text(self):
        raw = _make_openai_responses_sse(text="Hello world", input_tokens=20, output_tokens=42)
        blocks, usage = self.adapter.parse_sse(raw)
        text = "".join(b.content for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert "Hello" in text
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42

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
        blocks, usage = self.adapter.parse_sse(raw)
        call = next(b for b in blocks if b.block_type == BlockType.TOOL_CALL)
        assert call.tool_name == "search"
        assert call.tool_call_id == "fc1"
        assert call.content == '{"q":"test"}'

    def test_sse_empty_stream(self):
        blocks, usage = self.adapter.parse_sse(b"")
        assert blocks == []
        assert usage.input_tokens is None

    def test_opencode_zen_responses_path(self):
        raw = _make_openai_responses_sse(text="Hi", input_tokens=10, output_tokens=3)
        adapter = get_adapter("/zen/v1/responses")
        assert adapter is not None and adapter.format_id == "openai_responses"
        _, usage = adapter.parse_sse(raw)
        assert usage.output_tokens == 3

    # -- hidden reasoning -------------------------------------------------

    def test_hidden_reasoning_synthetic_block(self):
        """No reasoning item in output, but usage reports reasoning_tokens > 0."""
        resp = {
            "model": "o3",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
            "usage": {
                "input_tokens": 10, "output_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 200},
            },
        }
        blocks, usage = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert len(thinking) == 1
        assert thinking[0].content == ""
        assert thinking[0].content_hash is None
        assert thinking[0].token_count == 200
        assert thinking[0].attrs.get("hidden") is True
        assert usage.reasoning_tokens == 200

    def test_explicit_reasoning_item_not_duplicated(self):
        resp = {
            "model": "o3",
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "because X"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 50,
                      "output_tokens_details": {"reasoning_tokens": 200}},
        }
        blocks, usage = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert len(thinking) == 1
        assert thinking[0].content == "because X"


# ---------------------------------------------------------------------------
# Ollama adapter
# ---------------------------------------------------------------------------

class TestOllamaAdapter:
    def setup_method(self):
        self.adapter = OllamaAdapter()

    def test_request(self):
        blocks, tool_call_map = self.adapter.parse_request(OPENAI_REQ)
        assert any(b.block_type == BlockType.SYSTEM_PROMPT for b in blocks)
        assert tool_call_map == {}

    def test_response(self):
        blocks, usage = self.adapter.parse_response(OLLAMA_RESP)
        assert blocks[0].content == "Hello world"
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42

    def test_sse_ndjson(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Hello "}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": "world"}, "done": False}),
            json.dumps({"done": True, "prompt_eval_count": 20, "eval_count": 42}),
        ]
        raw = "\n".join(lines).encode()
        blocks, usage = self.adapter.parse_sse(raw)
        assert blocks[0].content == "Hello world"
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42


# ---------------------------------------------------------------------------
# classify_blocks / classify / per_tool_tokens
# ---------------------------------------------------------------------------

class TestClassify:
    def test_category_priority(self):
        adapter = AnthropicAdapter()
        req = {
            "model": "claude-sonnet-4-6",
            "system": "You are helpful.",
            "tools": [{"name": "search", "input_schema": {}}],
            "messages": [
                {"role": "user", "content": "first turn"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "result"},
                ]},
                {"role": "user", "content": "latest turn"},
            ],
        }
        input_blocks, tool_call_map = adapter.parse_request(req)
        classify_blocks(input_blocks)

        by_type = {b.block_type: b for b in input_blocks}
        assert by_type[BlockType.SYSTEM_PROMPT].category == "system_prompt"
        assert by_type[BlockType.TOOL_DEFINITION].category == "tool_definitions"
        assert by_type[BlockType.TOOL_RESULT].category == "tool_results"

        first_user = next(b for b in input_blocks if b.block_type == BlockType.USER_MESSAGE and b.content == "first turn")
        latest_user = next(b for b in input_blocks if b.block_type == BlockType.USER_MESSAGE and b.content == "latest turn")
        assert first_user.category == "conversation_history"
        assert latest_user.category == "current_user_message"

    def test_file_content_detection(self):
        adapter = AnthropicAdapter()
        big_file = "\n".join(f"line {i}" for i in range(60))
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": f"```python src/foo.py\n{big_file}\n```"},
            ],
        }
        input_blocks, _ = adapter.parse_request(req)
        classify_blocks(input_blocks)
        assert input_blocks[0].category == "file_contents"

    def test_assistant_prefill_category(self):
        adapter = AnthropicAdapter()
        req = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "continue the story"},
                {"role": "assistant", "content": "Once upon a time"},
            ],
        }
        input_blocks, _ = adapter.parse_request(req)
        classify_blocks(input_blocks)
        assistant_block = next(b for b in input_blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert assistant_block.category == "assistant_prefill"

    def test_classify_full_analyzed_request(self):
        adapter = AnthropicAdapter()
        input_blocks, tool_call_map = adapter.parse_request(ANTHROPIC_REQ)
        output_blocks, usage = adapter.parse_response(ANTHROPIC_RESP)
        analyzed = AnalyzedRequest(
            model=ANTHROPIC_REQ["model"], input_blocks=input_blocks,
            output_blocks=output_blocks, usage=usage, tool_call_map=tool_call_map,
        )
        breakdown = classify(analyzed)
        assert breakdown.total_input > 0
        assert breakdown.total_output == 2  # "Hello world" -> 2 tokens
        assert breakdown.tokens_output_text == 2
        assert breakdown.tokens_output_thinking == 0

    def test_per_tool_tokens_attribution(self):
        adapter = AnthropicAdapter()
        req = {
            "model": "claude-sonnet-4-6",
            "tools": [{"name": "search", "input_schema": {}}],
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "a fairly long result body"},
                ]},
            ],
        }
        input_blocks, tool_call_map = adapter.parse_request(req)
        analyzed = AnalyzedRequest(model=req["model"], input_blocks=input_blocks,
                                    output_blocks=[], usage=Usage(),
                                    tool_call_map=tool_call_map)
        rows = per_tool_tokens(analyzed)
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "search"
        assert rows[0]["result_tokens"] > 0


# ---------------------------------------------------------------------------
# Content-addressed persistence + retention GC
# ---------------------------------------------------------------------------

class TestBlockPersistence:
    def test_content_addressed_dedup(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.analysis.blocks import Block

        init_db(tmp_path / "dedup.db")
        shared_text = "You are a helpful coding assistant."
        b1 = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, shared_text)
        b2 = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, shared_text)
        assert b1.content_hash == b2.content_hash

        with get_db() as db:
            r1 = crud.create_request(db, {
                "id": "req1", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, r1.id, [b1])
            r2 = crud.create_request(db, {
                "id": "req2", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, r2.id, [b2])

        from contextspy.db.database import get_engine
        from sqlalchemy import text as sql_text
        with get_engine().connect() as conn:
            count = conn.execute(
                sql_text("SELECT COUNT(*) FROM block_contents WHERE hash = :h"), {"h": b1.content_hash}
            ).scalar()
        assert count == 1

        with get_db() as db:
            blocks_r1 = crud.get_blocks(db, "req1")
            blocks_r2 = crud.get_blocks(db, "req2")
        assert blocks_r1[0]["content"] == shared_text
        assert blocks_r2[0]["content"] == shared_text

    def test_tool_block_links(self, tmp_path):
        """tool_call/tool_result blocks link to their tool_definition; tool_result also
        links to its tool_call — resolved at read time via tool_name/tool_call_id, no
        stored FK column needed."""
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.analysis.blocks import Block

        init_db(tmp_path / "links.db")
        definition = Block.make(Direction.INPUT, BlockType.TOOL_DEFINITION,
                                 '{"name": "search"}', tool_name="search")
        call = Block.make(Direction.INPUT, BlockType.TOOL_CALL, '{"q": "x"}',
                           message_index=1, tool_name="search", tool_call_id="t1")
        result = Block.make(Direction.INPUT, BlockType.TOOL_RESULT, "result text",
                             message_index=2, tool_name="search", tool_call_id="t1")
        unrelated = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "hi", message_index=0)

        with get_db() as db:
            req = crud.create_request(db, {
                "id": "req-links", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req.id, [unrelated, definition, call, result])

        with get_db() as db:
            blocks = crud.get_blocks(db, "req-links")

        by_type = {b["block_type"]: b for b in blocks}
        definition_id = by_type[BlockType.TOOL_DEFINITION]["id"]
        call_id = by_type[BlockType.TOOL_CALL]["id"]

        assert by_type[BlockType.TOOL_CALL]["linked_definition_id"] == definition_id
        assert by_type[BlockType.TOOL_CALL]["linked_call_id"] is None
        assert by_type[BlockType.TOOL_RESULT]["linked_call_id"] == call_id
        assert by_type[BlockType.TOOL_RESULT]["linked_definition_id"] == definition_id
        assert by_type[BlockType.USER_MESSAGE]["linked_call_id"] is None
        assert by_type[BlockType.USER_MESSAGE]["linked_definition_id"] is None
        assert by_type[BlockType.TOOL_DEFINITION]["linked_call_id"] is None
        assert by_type[BlockType.TOOL_DEFINITION]["linked_definition_id"] is None

    def test_previous_message_chain(self, tmp_path):
        """user/assistant message blocks link back to the previous conversational turn,
        skipping over tool-only turns (call/result) and the system prompt in between."""
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.analysis.blocks import Block

        init_db(tmp_path / "prevmsg.db")
        system = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful.", message_index=-1)
        user0 = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "read the file", message_index=0)
        # message_index 1: a pure tool-call turn — no user/assistant message block here
        call1 = Block.make(Direction.INPUT, BlockType.TOOL_CALL, '{"path": "x"}',
                            message_index=1, tool_name="Read", tool_call_id="t1")
        result2 = Block.make(Direction.INPUT, BlockType.TOOL_RESULT, "file contents",
                              message_index=2, tool_name="Read", tool_call_id="t1")
        assistant3 = Block.make(Direction.INPUT, BlockType.ASSISTANT_MESSAGE, "here's the file", message_index=3)
        user4 = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "thanks, now edit it", message_index=4)

        with get_db() as db:
            req = crud.create_request(db, {
                "id": "req-chain", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req.id, [system, user0, call1, result2, assistant3, user4])

        with get_db() as db:
            blocks = crud.get_blocks(db, "req-chain")

        by_content = {b["content"]: b for b in blocks}
        user0_id = by_content["read the file"]["id"]
        assistant3_id = by_content["here's the file"]["id"]

        assert by_content["read the file"]["linked_previous_message_id"] is None
        assert by_content["here's the file"]["linked_previous_message_id"] == user0_id
        assert by_content["thanks, now edit it"]["linked_previous_message_id"] == assistant3_id
        # non-message blocks never get a previous-message link
        assert by_content["You are helpful."]["linked_previous_message_id"] is None
        assert by_content["file contents"]["linked_previous_message_id"] is None

    def test_retention_gc_keeps_shared_content(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db, startup_vacuum
        from contextspy.analysis.blocks import Block
        from contextspy.config import Settings

        init_db(tmp_path / "gc.db")
        shared = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "shared prompt")
        old_only = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "old only message")

        with get_db() as db:
            old_req = crud.create_request(db, {
                "id": "old1", "timestamp": datetime.now(timezone.utc) - timedelta(days=30),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, old_req.id, [shared, old_only])
            new_req = crud.create_request(db, {
                "id": "new1", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, new_req.id, [shared])

        settings = Settings()
        settings.retention.raw_body_days = 0
        settings.retention.block_content_days = 7
        startup_vacuum(settings)

        from contextspy.db.database import get_engine
        from sqlalchemy import text as sql_text
        with get_engine().connect() as conn:
            shared_count = conn.execute(
                sql_text("SELECT COUNT(*) FROM block_contents WHERE hash = :h"), {"h": shared.content_hash}
            ).scalar()
            old_only_count = conn.execute(
                sql_text("SELECT COUNT(*) FROM block_contents WHERE hash = :h"), {"h": old_only.content_hash}
            ).scalar()

        assert shared_count == 1, "content still referenced by a recent request must survive GC"
        assert old_only_count == 0, "content only referenced by an old request must be purged"


# ---------------------------------------------------------------------------
# Provider and agent detection  (addon routing) — unchanged by this refactor
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestProviderDetection:
    """Guards against accidentally breaking host→provider routing when adding new entries."""

    def test_claude_code(self):
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
        assert _detect_provider("api.githubcopilot.com", 443) == "copilot"

    def test_opencode_zen_gateway(self):
        assert _detect_provider("opencode.ai", 443) == "opencode_zen"

    def test_opencode_zen_subdomain(self):
        assert _detect_provider("api.opencode.ai", 443) == "opencode_zen"

    def test_ollama_port(self):
        assert _detect_provider("localhost", 11434) == "ollama"
        assert _detect_provider("127.0.0.1", 11434) == "ollama"

    def test_unknown_host_returns_none(self):
        assert _detect_provider("example.com", 443) is None

    def test_telemetry_hosts_return_none(self):
        assert _detect_provider("eu-central-1-1.aws.cloud2.influxdata.com", 443) is None
        assert _detect_provider("models.dev", 443) is None


@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestAgentDetection:
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
