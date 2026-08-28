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
from contextspy.analysis.capture import CapturedEvent, decode_sse
from contextspy.analysis.classifier import classify, classify_blocks, per_tool_tokens
from contextspy.analysis.tokenizer import count_tokens


def _parse_stream(adapter, raw: bytes):
    canonical = adapter.reconstruct_stream(raw)
    return adapter.parse_response(canonical.payload)


def _json_sse_payloads(raw: bytes) -> list[dict]:
    return [event.payload for event in decode_sse(raw) if isinstance(event.payload, dict)]

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
# generic SSE framing
# ---------------------------------------------------------------------------

class TestExtractSseEvents:
    def test_data_lines_parsed(self):
        raw = b'data: {"type": "a"}\n\ndata: {"type": "b"}\n\n'
        events = _json_sse_payloads(raw)
        assert events == [{"type": "a"}, {"type": "b"}]

    def test_done_sentinel_skipped(self):
        raw = b'data: {"type": "a"}\n\ndata: [DONE]\n\n'
        events = _json_sse_payloads(raw)
        assert events == [{"type": "a"}]


class TestCompleteSseCapture:
    def test_preserves_sse_metadata_multiline_and_done(self):
        raw = (
            b"event: custom\n"
            b"id: evt-1\n"
            b"retry: 1500\n"
            b": provider-extension\n"
            b"data: {\"type\":\n"
            b"data: \"custom\", \"unknown\": 7}\n\n"
            b"data: [DONE]\n\n"
        )
        events = decode_sse(raw)
        assert len(events) == 2
        assert events[0].event == "custom"
        assert events[0].event_id == "evt-1"
        assert events[0].retry_ms == 1500
        assert events[0].comments == ["provider-extension"]
        assert events[0].payload == {"type": "custom", "unknown": 7}
        assert events[1].done is True

    def test_preserves_non_json_data(self):
        events = decode_sse(b"data: provider-specific text\n\n")
        assert events[0].kind == "text"
        assert events[0].payload == "provider-specific text"

    def test_tolerates_json_per_line_without_blank_records(self):
        events = decode_sse(b'data: {"n": 1}\ndata: {"n": 2}\ndata: [DONE]')
        assert [event.payload for event in events[:2]] == [{"n": 1}, {"n": 2}]
        assert events[2].done is True


class TestCanonicalResponseReconstruction:
    @staticmethod
    def _block_signature(blocks):
        return [
            (b.block_type, b.content, b.tool_name, b.tool_call_id)
            for b in blocks
        ]

    def test_anthropic_stream_uses_buffered_parser(self):
        adapter = AnthropicAdapter()
        canonical = adapter.reconstruct_stream(_make_anthropic_sse())
        streamed_blocks, streamed_usage = adapter.parse_response(canonical.payload)
        assert self._block_signature(streamed_blocks)
        assert streamed_usage.output_tokens is not None
        assert canonical.payload["content"][0]["text"].strip() == "Hello world"
        assert canonical.events

    def test_openai_chat_reconstructs_all_choices_and_tool_arguments(self):
        adapter = OpenAIChatAdapter()
        payloads = [
            {"id": "chat-1", "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "Hi ", "tool_calls": [
                    {"index": 0, "id": "call-1", "function": {"name": "search", "arguments": '{"q":'}},
                ]}},
                {"index": 1, "delta": {"role": "assistant", "content": "Alternative"}},
            ]},
            {"choices": [
                {"index": 0, "delta": {"content": "there", "tool_calls": [
                    {"index": 0, "function": {"arguments": '"x"}'}},
                ]}, "finish_reason": "tool_calls"},
                {"index": 1, "delta": {}, "finish_reason": "stop"},
            ], "usage": {"prompt_tokens": 3, "completion_tokens": 4,
                           "provider_extension": {"kept": True}}},
        ]
        canonical = adapter.reconstruct_response(
            [CapturedEvent(i, payload=p) for i, p in enumerate(payloads)], transport="sse",
        )
        assert canonical.payload["object"] == "chat.completion"
        assert canonical.payload["choices"][0]["message"]["content"] == "Hi there"
        assert canonical.payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"q":"x"}'
        assert canonical.payload["choices"][1]["message"]["content"] == "Alternative"
        assert canonical.payload["usage"]["provider_extension"] == {"kept": True}
        blocks, _ = adapter.parse_response(canonical.payload)
        assert len([b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]) == 2

    def test_openai_chat_reconstructs_legacy_function_call(self):
        adapter = OpenAIChatAdapter()
        payloads = [
            {"choices": [{"index": 0, "delta": {
                "function_call": {"name": "search", "arguments": '{"q":'},
            }}]},
            {"choices": [{"index": 0, "delta": {
                "function_call": {"arguments": '"test"}'},
            }, "finish_reason": "function_call"}]},
        ]
        canonical = adapter.reconstruct_response(
            [CapturedEvent(i, payload=p) for i, p in enumerate(payloads)], transport="sse",
        )
        blocks, _ = adapter.parse_response(canonical.payload)
        call = next(block for block in blocks if block.block_type == BlockType.TOOL_CALL)
        assert call.tool_name == "search"
        assert call.content == '{"q":"test"}'

    def test_responses_completed_snapshot_and_deltas_are_combined(self):
        adapter = OpenAIResponsesAdapter()
        payloads = [
            {"type": "response.created", "response": {"id": "resp-1", "model": "gpt-5"}},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "message", "role": "assistant"}},
            {"type": "response.output_text.delta", "output_index": 0,
             "content_index": 0, "delta": "hello"},
            {"type": "provider.unknown", "future_field": {"kept": True}},
            {"type": "response.completed", "response": {
                "id": "resp-1", "model": "gpt-5",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }},
        ]
        canonical = adapter.reconstruct_response(
            [CapturedEvent(i, payload=p) for i, p in enumerate(payloads)], transport="websocket",
        )
        assert canonical.payload["output"][0]["content"][0]["text"] == "hello"
        assert canonical.events[3].payload["future_field"] == {"kept": True}
        blocks, usage = adapter.parse_response(canonical.payload)
        assert blocks[0].content == "hello"
        assert usage.output_tokens == 3

    def test_responses_failed_snapshot_is_terminal_and_preserved(self):
        adapter = OpenAIResponsesAdapter()
        failure = {
            "type": "response.failed",
            "response": {
                "id": "resp-failed",
                "status": "failed",
                "error": {"code": "server_error", "message": "failed"},
                "output": [],
            },
        }
        canonical = adapter.reconstruct_response(
            [CapturedEvent(0, payload=failure)], transport="websocket",
        )
        assert canonical.complete is True
        assert canonical.payload["status"] == "failed"
        assert canonical.payload["error"]["code"] == "server_error"

    def test_ollama_ndjson_reconstructs_canonical_response(self):
        adapter = OllamaAdapter()
        raw = b'\n'.join([
            b'{"model":"llama","message":{"role":"assistant","content":"Hi "},"done":false}',
            b'{"message":{"role":"assistant","content":"there"},"done":false}',
            b'{"done":true,"prompt_eval_count":2,"eval_count":3,"future":"kept"}',
        ])
        canonical = adapter.reconstruct_stream(raw)
        assert canonical.payload["message"]["content"] == "Hi there"
        assert canonical.payload["future"] == "kept"
        blocks, usage = adapter.parse_response(canonical.payload)
        assert blocks[0].content == "Hi there"
        assert usage == Usage(input_tokens=2, output_tokens=3)

class TestExtractSseEventsAdditional:
    def test_blank_lines_skipped(self):
        raw = b'\n\ndata: {"type": "a"}\n\n\n'
        events = _json_sse_payloads(raw)
        assert events == [{"type": "a"}]

    def test_bad_json_skipped(self):
        raw = b'data: not json\n\ndata: {"type": "a"}\n\n'
        events = _json_sse_payloads(raw)
        assert events == [{"type": "a"}]

    def test_empty_input(self):
        assert _json_sse_payloads(b"") == []

    def test_non_data_lines_ignored(self):
        raw = b'event: message_start\n\ndata: {"type": "a"}\n\n'
        events = _json_sse_payloads(raw)
        assert events == [{"type": "a"}]


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
        blocks, usage = _parse_stream(self.adapter, raw)
        assert usage.input_tokens == 610
        assert usage.output_tokens == 42
        assert usage.cache_read_tokens == 500
        assert usage.cache_creation_tokens == 100
        text = "".join(b.content for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE)
        assert "Hello" in text and "world" in text

    def test_copilot_bedrock_stream_all_tokens_in_message_delta(self):
        raw = _make_copilot_claude_sse(text="Hi there", input_tokens=1, output_tokens=220,
                                        cache_read=40876, cache_creation=3926)
        blocks, usage = _parse_stream(self.adapter, raw)
        assert usage.output_tokens == 220
        assert usage.cache_read_tokens == 40876
        assert usage.cache_creation_tokens == 3926

    def test_empty_stream(self):
        blocks, usage = _parse_stream(self.adapter, b"")
        assert blocks == []

    def test_copilot_claude_sse_via_get_adapter(self):
        """endpoint=/v1/messages must dispatch to the Anthropic adapter regardless of host."""
        raw = _make_copilot_claude_sse(text="Hello world", input_tokens=1,
                                        output_tokens=220, cache_read=40876, cache_creation=3926)
        adapter = get_adapter("/v1/messages")
        assert adapter is not None
        blocks, usage = _parse_stream(adapter, raw)
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
        blocks, usage = _parse_stream(self.adapter, raw)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        text = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert thinking and thinking[0].content == "step 1 step 2"
        assert thinking[0].attrs.get("signature") == "abc"
        assert text and text[0].content == "final answer"

    def test_response_omitted_display_thinking_tokens_derived(self):
        # thinking.display: "omitted" (the default on current Claude models)
        # returns a real thinking block with an empty text field. The Messages
        # API reports no per-turn thinking count anywhere, so the only signal
        # is the part of output_tokens the visible text does not account for.
        resp = {
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 50},
        }
        blocks, usage = self.adapter.parse_response(resp)
        visible = sum(
            b.token_count for b in blocks if b.block_type != BlockType.THINKING
        )
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == ""
        assert thinking[0].token_count == 50 - visible
        assert thinking[0].attrs.get("hidden") is True
        assert thinking[0].attrs.get("token_source") == "derived"
        # Usage stays strictly provider-reported — Anthropic reported nothing.
        assert usage.reasoning_tokens is None

    def test_redacted_thinking_tokens_derived(self):
        resp = {
            "content": [
                {"type": "redacted_thinking", "data": "encrypted-blob"},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 50},
        }
        blocks, _ = self.adapter.parse_response(resp)
        visible = sum(
            b.token_count for b in blocks if b.block_type != BlockType.THINKING
        )
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].token_count == 50 - visible
        assert thinking[0].attrs.get("redacted") is True
        assert thinking[0].attrs.get("token_source") == "derived"

    def test_summarized_thinking_tokens_estimated(self):
        # display: "summarized" returns real text — the tokenizer estimate on
        # that text stands, rather than being overwritten by a derivation.
        resp = {
            "content": [
                {"type": "thinking", "thinking": "a fairly long chain of reasoning"},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 50},
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == "a fairly long chain of reasoning"
        assert thinking[0].token_count == count_tokens(thinking[0].content)
        assert thinking[0].attrs.get("token_source") == "estimated"
        assert thinking[0].attrs.get("hidden") is None

    def test_no_thinking_block_leaves_output_alone(self):
        resp = {
            "content": [{"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 5, "output_tokens": 50},
        }
        blocks, _ = self.adapter.parse_response(resp)
        assert not [b for b in blocks if b.block_type == BlockType.THINKING]

    def test_derivation_skipped_when_visible_output_exceeds_total(self):
        # Tokenizer estimate for the visible text already exceeds the reported
        # output_tokens — there is no residual to attribute, so don't invent one.
        resp = {
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "a much longer answer than the count suggests"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].token_count == 0
        assert thinking[0].attrs.get("token_source") == "unknown"

    def test_sse_omitted_display_thinking_tokens_derived(self):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "final answer"}},
            {"type": "message_delta", "usage": {"output_tokens": 50}},
        ]
        raw = b"\n".join(b"data: " + json.dumps(e).encode() for e in events)
        blocks, usage = _parse_stream(self.adapter, raw)
        visible = sum(
            b.token_count for b in blocks if b.block_type != BlockType.THINKING
        )
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == ""
        assert thinking[0].token_count == 50 - visible
        assert thinking[0].attrs.get("hidden") is True
        assert thinking[0].attrs.get("token_source") == "derived"
        assert usage.reasoning_tokens is None

    def test_derived_thinking_reaches_the_breakdown(self):
        # End-to-end: the whole point of deriving is that tokens_output_thinking
        # stops reading as zero, and the output side now fully accounts for
        # what the provider billed.
        resp = {
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "answer"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 50},
        }
        blocks, usage = self.adapter.parse_response(resp)
        analyzed = AnalyzedRequest(
            model="claude-opus-5", input_blocks=[], output_blocks=blocks, usage=usage,
        )
        breakdown = classify(analyzed)
        assert breakdown.tokens_output_thinking > 0
        assert breakdown.total_output == 50

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

    def test_tool_result_with_tool_reference_is_preserved(self):
        """Claude Code can return structural references inside tool results."""
        req = {
            "model": "claude-sonnet-5",
            "messages": [
                {"role": "assistant", "content": [{
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": [{
                        "type": "tool_reference",
                        "tool_name": "read_file",
                    }],
                }]},
            ],
        }

        blocks, tool_call_map = self.adapter.parse_request(req)

        result = next(b for b in blocks if b.block_type == BlockType.TOOL_RESULT)
        assert json.loads(result.content) == {
            "type": "tool_reference",
            "tool_name": "read_file",
        }
        assert result.tool_name == "read_file"
        assert tool_call_map == {"call-1": "read_file"}

    def test_system_role_inside_messages_is_a_system_prompt(self):
        req = {
            "model": "claude-sonnet-5",
            "messages": [
                {"role": "user", "content": "Start"},
                {"role": "system", "content": "Use the following tool reference."},
            ],
        }

        blocks, _ = self.adapter.parse_request(req)

        system = next(b for b in blocks if b.content == "Use the following tool reference.")
        assert system.block_type == BlockType.SYSTEM_PROMPT


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
        blocks, usage = _parse_stream(self.adapter, raw)
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

    def test_response_reasoning_content(self):
        resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello world",
                    "reasoning_content": "The user said hello, I should greet back.",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 42},
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == "The user said hello, I should greet back."

    def test_response_hidden_reasoning_tokens_synthesized(self):
        resp = {
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 20, "completion_tokens": 42,
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
        }
        blocks, usage = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert thinking and thinking[0].content == "" and thinking[0].token_count == 30
        assert usage.reasoning_tokens == 30

    def test_sse_reasoning_content_delta(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"reasoning_content": "Thinking "}}]},
            {"choices": [{"index": 0, "delta": {"reasoning_content": "it over."}}]},
            {"choices": [{"index": 0, "delta": {"content": "Hello world"}}]},
        ]
        lines = ["data: " + json.dumps(c) for c in chunks]
        lines.append("data: [DONE]")
        raw = "\n".join(lines).encode()
        blocks, _ = _parse_stream(self.adapter, raw)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        text = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert thinking and thinking[0].content == "Thinking it over."
        assert text and text[0].content == "Hello world"


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

    def test_namespace_tools_become_individual_definitions(self):
        namespace = {
            "type": "namespace",
            "name": "functions",
            "description": "General purpose tools",
            "tools": [
                {"type": "function", "name": "wait", "parameters": {}},
                {"type": "function", "name": "request_user_input", "parameters": {}},
                {"type": "custom", "name": "exec", "format": {"type": "text"}},
            ],
        }

        blocks, _ = self.adapter.parse_request({"tools": [namespace]})
        definitions = [b for b in blocks if b.block_type == BlockType.TOOL_DEFINITION]

        assert [b.tool_name for b in definitions] == ["wait", "request_user_input", "exec"]
        assert all(b.attrs.get("tool_namespace") == "functions" for b in definitions)
        assert sum(b.token_count for b in definitions) == count_tokens(
            json.dumps(namespace, ensure_ascii=False)
        )

    def test_repeated_namespace_definitions_aggregate_by_callable_name(self):
        namespace = {
            "type": "namespace",
            "name": "functions",
            "tools": [
                {"type": "function", "name": "wait", "parameters": {}},
                {"type": "custom", "name": "exec", "format": {"type": "text"}},
            ],
        }
        req = {
            "tools": [namespace],
            "input": [
                {"type": "additional_tools", "tools": [namespace]},
                {"type": "custom_tool_call", "call_id": "call-1", "name": "exec", "input": "pwd"},
                {"type": "custom_tool_call_output", "call_id": "call-1", "output": "/project"},
            ],
        }

        input_blocks, tool_call_map = self.adapter.parse_request(req)
        analyzed = AnalyzedRequest(
            model="gpt-test",
            input_blocks=input_blocks,
            output_blocks=[],
            usage=Usage(),
            tool_call_map=tool_call_map,
        )
        rows = per_tool_tokens(analyzed)

        assert [row["tool_name"] for row in rows] == ["wait", "exec"]
        assert rows[1]["result_tokens"] > 0
        assert sum(row["definition_tokens"] for row in rows) == sum(
            block.token_count
            for block in input_blocks
            if block.block_type == BlockType.TOOL_DEFINITION
        )

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
        blocks, usage = _parse_stream(self.adapter, raw)
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
        blocks, usage = _parse_stream(self.adapter, raw)
        call = next(b for b in blocks if b.block_type == BlockType.TOOL_CALL)
        assert call.tool_name == "search"
        assert call.tool_call_id == "fc1"
        assert call.content == '{"q":"test"}'

    def test_sse_empty_stream(self):
        blocks, usage = _parse_stream(self.adapter, b"")
        assert blocks == []
        assert usage.input_tokens is None

    def test_sse_and_websocket_events_use_equivalent_canonical_analysis(self):
        raw = _make_openai_responses_sse(text="Hello world", input_tokens=20, output_tokens=42)
        sse = self.adapter.reconstruct_stream(raw)
        websocket = self.adapter.reconstruct_response(decode_sse(raw), transport="websocket")
        sse_blocks, sse_usage = self.adapter.parse_response(sse.payload)
        event_blocks, event_usage = self.adapter.parse_response(websocket.payload)
        assert [b.content for b in sse_blocks] == [b.content for b in event_blocks]
        assert [b.block_type for b in sse_blocks] == [b.block_type for b in event_blocks]
        assert sse_usage == event_usage

    def test_opencode_zen_responses_path(self):
        raw = _make_openai_responses_sse(text="Hi", input_tokens=10, output_tokens=3)
        adapter = get_adapter("/zen/v1/responses")
        assert adapter is not None and adapter.format_id == "openai_responses"
        _, usage = _parse_stream(adapter, raw)
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
        # The summary is far shorter than the reasoning actually billed, so the
        # reported count wins over the tokenizer estimate on the summary text.
        assert thinking[0].token_count == 200
        assert thinking[0].attrs.get("token_source") == "provider"

    def test_empty_reasoning_item_still_gets_reported_tokens(self):
        """An empty reasoning item used to swallow the reported count entirely:
        it suppressed the synthetic block while contributing 0 tokens itself."""
        resp = {
            "model": "o3",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 50,
                      "output_tokens_details": {"reasoning_tokens": 200}},
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        assert len(thinking) == 1
        assert thinking[0].token_count == 200
        assert thinking[0].attrs.get("hidden") is True
        assert thinking[0].attrs.get("token_source") == "provider"


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
        blocks, usage = _parse_stream(self.adapter, raw)
        assert blocks[0].content == "Hello world"
        assert usage.input_tokens == 20
        assert usage.output_tokens == 42

    def test_response_thinking_field(self):
        resp = {
            "message": {"role": "assistant", "content": "Hello world", "thinking": "Let me think..."},
            "prompt_eval_count": 20, "eval_count": 42,
        }
        blocks, _ = self.adapter.parse_response(resp)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        text = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert thinking and thinking[0].content == "Let me think..."
        assert text and text[0].content == "Hello world"

    def test_sse_ndjson_thinking(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "", "thinking": "Let me "}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": "", "thinking": "think..."}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": "Hello world"}, "done": False}),
            json.dumps({"done": True, "prompt_eval_count": 20, "eval_count": 42}),
        ]
        raw = "\n".join(lines).encode()
        blocks, _ = _parse_stream(self.adapter, raw)
        thinking = [b for b in blocks if b.block_type == BlockType.THINKING]
        text = [b for b in blocks if b.block_type == BlockType.ASSISTANT_MESSAGE]
        assert thinking and thinking[0].content == "Let me think..."
        assert text and text[0].content == "Hello world"


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

    def test_first_seen_session_seq(self, tmp_path):
        """first_seen_session_seq reports the earliest session_seq (within the same
        session) a piece of content — matched by content_hash — appeared at, not the
        session_seq of the request being read."""
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.analysis.blocks import Block

        init_db(tmp_path / "first_seen.db")
        system_prompt = Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful.")
        turn1 = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "first turn", message_index=0)
        turn2 = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "second turn", message_index=1)
        turn3 = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "third turn", message_index=2)

        with get_db() as db:
            session = crud.create_session(db, "s1")

            req1 = crud.create_request(db, {
                "id": "fs-req1", "session_id": session.id, "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req1.id, [
                Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful."),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "first turn", message_index=0),
            ])

            req2 = crud.create_request(db, {
                "id": "fs-req2", "session_id": session.id, "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req2.id, [
                Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful."),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "first turn", message_index=0),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "second turn", message_index=1),
            ])

            req3 = crud.create_request(db, {
                "id": "fs-req3", "session_id": session.id, "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req3.id, [
                Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful."),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "first turn", message_index=0),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "second turn", message_index=1),
                Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "third turn", message_index=2),
            ])

            # Same content in a different session must not leak into session 1's
            # first-seen calculation.
            other_session = crud.create_session(db, "s2")
            req_other = crud.create_request(db, {
                "id": "fs-req-other", "session_id": other_session.id, "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req_other.id, [
                Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful."),
            ])

            # A request with no session at all — first_seen_session_seq must be None.
            req_no_session = crud.create_request(db, {
                "id": "fs-req-nosession", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            crud.insert_blocks(db, req_no_session.id, [
                Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, "You are helpful."),
            ])

            assert req1.session_seq == 1
            assert req2.session_seq == 2
            assert req3.session_seq == 3
            assert req_other.session_seq == 1

        with get_db() as db:
            blocks_req3 = crud.get_blocks(db, "fs-req3")
            blocks_other = crud.get_blocks(db, "fs-req-other")
            blocks_no_session = crud.get_blocks(db, "fs-req-nosession")

        by_content_req3 = {b["content"]: b for b in blocks_req3}
        assert by_content_req3["You are helpful."]["first_seen_session_seq"] == 1
        assert by_content_req3["first turn"]["first_seen_session_seq"] == 1
        assert by_content_req3["second turn"]["first_seen_session_seq"] == 2
        assert by_content_req3["third turn"]["first_seen_session_seq"] == 3

        # Session 2 saw the (identical) system prompt for the first time in its own
        # request #1, unaffected by session 1 having seen it back at #1 too.
        assert blocks_other[0]["first_seen_session_seq"] == 1

        assert blocks_no_session[0]["first_seen_session_seq"] is None

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
# transport column (native WS capture)
# ---------------------------------------------------------------------------

class TestTransportColumn:
    def test_websocket_transport_round_trips(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db

        init_db(tmp_path / "transport.db")
        with get_db() as db:
            req = crud.create_request(db, {
                "id": "req-ws", "timestamp": datetime.now(timezone.utc),
                "provider": "openai_chatgpt", "endpoint": "/backend-api/codex/responses",
                "transport": "websocket",
            })
            assert req.transport == "websocket"

        with get_db() as db:
            fetched = crud.get_request(db, "req-ws")
            assert fetched.transport == "websocket"
            assert fetched.to_dict()["transport"] == "websocket"

    def test_transport_defaults_to_http_when_omitted(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db

        init_db(tmp_path / "transport_default.db")
        with get_db() as db:
            req = crud.create_request(db, {
                "id": "req-http", "timestamp": datetime.now(timezone.utc),
                "provider": "anthropic", "endpoint": "/v1/messages",
            })
            assert req.transport == "http"
            assert req.response_transport == "legacy"
            assert bool(req.response_complete) is False

    def test_response_capture_metadata_round_trips(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db

        init_db(tmp_path / "response_capture.db")
        events = [{
            "sequence": 0,
            "direction": "server_to_client",
            "kind": "json",
            "payload": {"type": "response.output_text.delta", "delta": "hi"},
            "text": None,
            "event": None,
            "event_id": None,
            "retry_ms": None,
            "done": False,
        }]
        with get_db() as db:
            crud.create_request(db, {
                "id": "req-sse", "timestamp": datetime.now(timezone.utc),
                "provider": "openai", "endpoint": "/v1/responses",
                "response_transport": "sse",
                "response_reconstructed": 1,
                "response_complete": 0,
                "capture_error": json.dumps({"stage": "transport", "message": "truncated"}),
                "raw_response_body": json.dumps({"id": "resp_1", "output": []}),
                "response_events": json.dumps(events),
            })

        with get_db() as db:
            payload = crud.get_request(db, "req-sse").to_dict()

        assert payload["response_transport"] == "sse"
        assert payload["response_reconstructed"] is True
        assert payload["response_complete"] is False
        assert payload["capture_error"] == {"stage": "transport", "message": "truncated"}
        assert payload["response_events"] == events

    def test_retention_purges_response_events_with_canonical_bodies(self, tmp_path):
        from contextspy.config import Settings
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db, startup_vacuum

        init_db(tmp_path / "response_retention.db")
        with get_db() as db:
            crud.create_request(db, {
                "id": "req-old-stream",
                "timestamp": datetime.now(timezone.utc) - timedelta(days=30),
                "provider": "openai", "endpoint": "/v1/responses",
                "raw_request_body": json.dumps({"input": "secret"}),
                "raw_response_body": json.dumps({"output": "secret"}),
                "response_events": json.dumps([{"payload": {"delta": "secret"}}]),
            })

        settings = Settings()
        settings.retention.raw_body_days = 7
        settings.retention.block_content_days = 0
        startup_vacuum(settings)

        with get_db() as db:
            payload = crud.get_request(db, "req-old-stream").to_dict()

        assert payload["raw_request_body"] is None
        assert payload["raw_response_body"] is None
        assert payload["response_events"] is None

    def test_init_db_twice_is_idempotent(self, tmp_path):
        from contextspy.db.database import init_db

        db_path = tmp_path / "reopen.db"
        init_db(db_path)
        init_db(db_path)  # migration must not raise on a DB that already has the column


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

    def test_codex_cli_rs(self):
        assert _detect_agent("codex_cli_rs/0.46.0") == "codex"

    def test_unknown(self):
        assert _detect_agent("curl/7.88.1") == "unknown"
        assert _detect_agent("") == "unknown"


# ---------------------------------------------------------------------------
# Addon-level WS exchange persistence (no mitmproxy master needed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestHandleWsExchange:
    def test_persists_websocket_request(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.proxy.addon import ContextSpyAddon, _WsFlowState
        from contextspy.proxy.ws_protocols import CompletedExchange

        init_db(tmp_path / "ws_exchange.db")

        request_body = {
            "type": "response.create",
            "model": "gpt-5-codex",
            "input": [{"role": "user", "content": "Say hello"}],
        }
        events = [
            {"type": "response.output_text.delta", "output_index": 0, "delta": "Hello world"},
            {"type": "response.completed", "response": {
                "model": "gpt-5-codex", "usage": {"input_tokens": 20, "output_tokens": 42},
            }},
        ]
        exchange = CompletedExchange(
            request_body=request_body,
            raw_request_text=json.dumps(request_body),
            events=[
                CapturedEvent(sequence=i, payload=event)
                for i, event in enumerate(events)
            ],
            request_ts=1.0, first_event_ts=1.2, last_event_ts=1.5,
        )
        state = _WsFlowState(
            session=None, provider="openai_chatgpt", agent="codex",
            endpoint="/backend-api/codex/responses",
        )

        addon = ContextSpyAddon()
        addon._handle_ws_exchange(state, exchange)

        with get_db() as db:
            rows = crud.list_requests(db)
            assert len(rows) == 1
            row = rows[0]
            assert row.transport == "websocket"
            assert row.provider == "openai_chatgpt"
            assert row.agent == "codex"
            assert row.status_code is None
            assert row.duration_ms == 500
            assert row.ttft_ms in (199, 200)
            assert row.tokens_total_output > 0
            assert row.response_transport == "websocket"
            assert bool(row.response_reconstructed) is True
            assert bool(row.response_complete) is True
            assert json.loads(row.raw_response_body)["output"][0]["content"][0]["text"] == "Hello world"
            normalized_events = json.loads(row.response_events)
            assert [event["payload"] for event in normalized_events] == events


@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestAddonCaptureBoundaries:
    @staticmethod
    def _flow(*, request_text: str, response_text: str | None = None, error=None):
        from types import SimpleNamespace

        request = SimpleNamespace(
            pretty_host="api.openai.com",
            port=443,
            path="/v1/responses",
            headers={"user-agent": "test"},
            get_text=lambda: request_text,
        )
        response = None if response_text is None else SimpleNamespace(
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            get_text=lambda: response_text,
        )
        return SimpleNamespace(
            id="flow-1", request=request, response=response, websocket=None,
            metadata={"contextspy_request_body": request_text}, error=error,
        )

    def test_stream_capture_survives_both_analysis_failures(self, tmp_path, monkeypatch):
        from contextspy.analysis.capture import CanonicalResponse
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.proxy import addon as addon_module

        class FailingAnalysisAdapter:
            format_id = "test"
            stream_format = "sse"

            def reconstruct_response(self, events, *, transport):
                return CanonicalResponse(
                    payload={"id": "canonical", "unknown": {"kept": True}},
                    transport=transport,
                    events=events,
                    reconstructed=True,
                    complete=True,
                )

            def parse_request(self, request):
                raise ValueError("request parser failed")

            def parse_response(self, response):
                raise ValueError("response parser failed")

        init_db(tmp_path / "capture_boundary.db")
        monkeypatch.setattr(addon_module, "get_adapter", lambda endpoint: FailingAnalysisAdapter())
        flow = self._flow(
            request_text=json.dumps({"model": "gpt-test", "input": "hello"}),
            response_text='data: {"type":"future.event","value":7}\n\ndata: [DONE]\n\n',
        )

        addon_module.ContextSpyAddon(provider_override="openai")._handle_response(flow)

        with get_db() as db:
            rows = crud.list_requests(db)
            assert len(rows) == 1
            detail = rows[0].to_dict()
        assert json.loads(detail["raw_response_body"]) == {
            "id": "canonical", "unknown": {"kept": True},
        }
        assert detail["response_reconstructed"] is True
        assert detail["response_complete"] is True
        assert detail["response_events"][0]["payload"] == {
            "type": "future.event", "value": 7,
        }
        assert detail["response_events"][1]["done"] is True
        assert detail["capture_error"]["stage"] == "request_analysis"
        assert detail["capture_error"]["additional"][0]["stage"] == "response_analysis"

    def test_http_error_retains_request_and_does_not_double_save(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.proxy.addon import ContextSpyAddon

        init_db(tmp_path / "failed_http.db")
        request_text = json.dumps({"model": "gpt-test", "input": "keep me"})
        flow = self._flow(
            request_text=request_text,
            error="connection reset before response",
        )
        addon = ContextSpyAddon(provider_override="openai")
        addon.error(flow)
        addon.error(flow)

        with get_db() as db:
            rows = crud.list_requests(db)
            assert len(rows) == 1
            detail = rows[0].to_dict()
        assert detail["raw_request_body"] == request_text
        assert detail["raw_response_body"] is None
        assert detail["response_transport"] == "none"
        assert detail["response_complete"] is False
        assert detail["capture_error"]["stage"] == "transport"
