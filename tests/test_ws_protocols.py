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
"""Tests for contextspy.proxy.ws_protocols — the WS protocol registry and the
Codex CLI (ChatGPT-plan) frame-stream assembler."""
from __future__ import annotations

import json

from contextspy.proxy.ws_protocols import get_ws_protocol
from contextspy.proxy.ws_protocols.codex import CodexResponsesSession


# ---------------------------------------------------------------------------
# Fixtures — Codex WS frames
# ---------------------------------------------------------------------------

def _make_codex_request_frame(input_text: str = "Hello", model: str = "gpt-5-codex") -> tuple[dict, str]:
    obj = {
        "type": "response.create",
        "model": model,
        "input": [{"role": "user", "content": input_text}],
    }
    return obj, json.dumps(obj)


def _make_codex_event_frames(
    text: str = "Hello world", input_tokens: int = 20, output_tokens: int = 42,
    model: str = "gpt-5-codex",
) -> list[dict]:
    events: list[dict] = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "message", "role": "assistant"}},
    ]
    for word in text.split():
        events.append({
            "type": "response.output_text.delta", "output_index": 0, "delta": word + " ",
        })
    events.append({
        "type": "response.completed",
        "response": {"model": model, "usage": {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        }},
    })
    return events


def _feed(session: CodexResponsesSession, *, from_client: bool, obj: dict, timestamp: float,
          raw_text: str | None = None) -> list:
    content = (raw_text if raw_text is not None else json.dumps(obj)).encode()
    return session.on_message(from_client=from_client, content=content, is_text=True, timestamp=timestamp)


def _payloads(exchange) -> list:
    return [event.payload for event in exchange.events]


# ---------------------------------------------------------------------------
# WS_REGISTRY / get_ws_protocol
# ---------------------------------------------------------------------------

class TestGetWsProtocol:
    def test_match(self):
        protocol = get_ws_protocol("chatgpt.com", "/backend-api/codex/responses")
        assert protocol is not None
        assert protocol.protocol_id == "codex_responses"

    def test_wrong_host(self):
        assert get_ws_protocol("api.openai.com", "/backend-api/codex/responses") is None

    def test_wrong_path(self):
        assert get_ws_protocol("chatgpt.com", "/backend-api/other") is None

    def test_subdomain_suffix_matches(self):
        protocol = get_ws_protocol("foo.chatgpt.com", "/backend-api/codex/responses")
        assert protocol is not None
        assert protocol.protocol_id == "codex_responses"

    def test_unrelated_host_and_path(self):
        assert get_ws_protocol("example.com", "/") is None


# ---------------------------------------------------------------------------
# CodexResponsesSession
# ---------------------------------------------------------------------------

class TestCodexSession:
    def test_single_turn_completes_on_response_completed(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        result = _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)
        assert result == []

        events = _make_codex_event_frames()
        for i, event in enumerate(events[:-1]):
            result = _feed(session, from_client=False, obj=event, timestamp=2.0 + i * 0.1)
            assert result == [], "no exchange should complete before response.completed"

        result = _feed(session, from_client=False, obj=events[-1], timestamp=3.0)
        assert len(result) == 1
        ex = result[0]
        assert ex.request_body == obj
        assert ex.raw_request_text == text
        assert _payloads(ex) == events
        assert all(event.direction == "server_to_client" for event in ex.events)
        assert ex.complete is True
        assert ex.error is None
        assert ex.request_ts == 1.0
        assert ex.first_event_ts == 2.0
        assert ex.last_event_ts == 3.0

    def test_multi_turn_no_event_bleed(self):
        session = CodexResponsesSession()

        obj1, text1 = _make_codex_request_frame(input_text="first")
        _feed(session, from_client=True, obj=obj1, timestamp=1.0, raw_text=text1)
        events1 = _make_codex_event_frames(text="one")
        result = None
        for i, event in enumerate(events1):
            result = _feed(session, from_client=False, obj=event, timestamp=2.0 + i * 0.1)
        assert len(result) == 1
        ex1 = result[0]
        assert ex1.request_body == obj1
        assert _payloads(ex1) == events1

        obj2, text2 = _make_codex_request_frame(input_text="second")
        flushed = _feed(session, from_client=True, obj=obj2, timestamp=10.0, raw_text=text2)
        assert flushed == []  # first turn already completed cleanly, nothing dangling
        events2 = _make_codex_event_frames(text="two")
        result = None
        for i, event in enumerate(events2):
            result = _feed(session, from_client=False, obj=event, timestamp=11.0 + i * 0.1)
        assert len(result) == 1
        ex2 = result[0]
        assert ex2.request_body == obj2
        assert _payloads(ex2) == events2
        # no bleed of the first turn's events into the second
        assert _payloads(ex2) != _payloads(ex1)

    def test_rate_limits_retained_mid_turn_and_idle_server_frame_ignored(self):
        session = CodexResponsesSession()

        # idle server frame with no turn in flight
        idle = _feed(session, from_client=False, obj={"type": "codex.rate_limits"}, timestamp=0.5)
        assert idle == []

        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)

        # A rate_limits frame arriving mid-turn is not analyzed as output, but
        # remains part of the complete normalized provider event capture.
        result = _feed(session, from_client=False, obj={"type": "codex.rate_limits"}, timestamp=1.5)
        assert result == []

        events = _make_codex_event_frames()
        result = None
        for i, event in enumerate(events):
            result = _feed(session, from_client=False, obj=event, timestamp=2.0 + i * 0.1)
        assert len(result) == 1
        assert _payloads(result[0]) == [{"type": "codex.rate_limits"}, *events]

    def test_error_envelope_finalizes_immediately(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)

        error_frame = {
            "type": "error", "status": 429,
            "error": {"code": "rate_limited", "message": "Too many requests"},
        }
        result = _feed(session, from_client=False, obj=error_frame, timestamp=2.0)
        assert len(result) == 1
        ex = result[0]
        assert ex.complete is True
        assert ex.error == {"status": 429, "code": "rate_limited", "message": "Too many requests"}
        assert _payloads(ex) == [error_frame]

    def test_response_failed_is_a_complete_terminal_capture(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)
        failed = {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"code": "server_error", "message": "Provider failed"},
            },
        }
        result = _feed(session, from_client=False, obj=failed, timestamp=2.0)

        assert len(result) == 1
        assert result[0].complete is True
        assert result[0].error == {
            "status": None, "code": "server_error", "message": "Provider failed",
        }
        assert _payloads(result[0]) == [failed]

    def test_new_request_flushes_dangling_as_incomplete(self):
        session = CodexResponsesSession()
        obj1, text1 = _make_codex_request_frame(input_text="first")
        _feed(session, from_client=True, obj=obj1, timestamp=1.0, raw_text=text1)

        partial_event = {"type": "response.output_text.delta", "output_index": 0, "delta": "partial"}
        _feed(session, from_client=False, obj=partial_event, timestamp=2.0)

        obj2, text2 = _make_codex_request_frame(input_text="second")
        flushed = _feed(session, from_client=True, obj=obj2, timestamp=3.0, raw_text=text2)
        assert len(flushed) == 1
        dangling = flushed[0]
        assert dangling.request_body == obj1
        assert dangling.complete is False
        assert _payloads(dangling) == [partial_event]

    def test_close_flushes_dangling(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)

        result = session.on_close()
        assert len(result) == 1
        assert result[0].complete is False
        assert result[0].request_body == obj

    def test_close_with_no_pending_turn_is_empty(self):
        session = CodexResponsesSession()
        assert session.on_close() == []

    def test_binary_frame_ignored(self):
        session = CodexResponsesSession()
        result = session.on_message(
            from_client=True, content=b"\x00\x01\x02", is_text=False, timestamp=1.0,
        )
        assert result == []

    def test_garbage_json_ignored(self):
        session = CodexResponsesSession()
        result = session.on_message(
            from_client=True, content=b"not valid json{", is_text=True, timestamp=1.0,
        )
        assert result == []

    def test_non_dict_json_ignored(self):
        session = CodexResponsesSession()
        result = session.on_message(
            from_client=True, content=b"[1, 2, 3]", is_text=True, timestamp=1.0,
        )
        assert result == []

    def test_unknown_text_json_and_binary_are_retained_during_turn(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)

        session.on_message(
            from_client=False, content=b"not-json", is_text=True, timestamp=1.1,
        )
        session.on_message(
            from_client=False, content=b"[1,2,3]", is_text=True, timestamp=1.2,
        )
        session.on_message(
            from_client=False, content=b"\x00\x01", is_text=False, timestamp=1.3,
        )
        result = _feed(
            session,
            from_client=False,
            obj={"type": "response.completed", "response": {"output": []}},
            timestamp=2.0,
        )

        assert len(result) == 1
        events = result[0].events
        assert [(event.kind, event.payload, event.text) for event in events[:2]] == [
            ("text", None, "not-json"),
            ("json", [1, 2, 3], None),
        ]
        assert events[2].kind == "binary"
        assert events[2].payload == {"encoding": "base64", "data": "AAE="}

    def test_auxiliary_client_frame_is_retained_but_not_response_input(self):
        session = CodexResponsesSession()
        obj, text = _make_codex_request_frame()
        _feed(session, from_client=True, obj=obj, timestamp=1.0, raw_text=text)
        _feed(
            session, from_client=True, obj={"type": "client.control", "value": 1},
            timestamp=1.1,
        )
        result = _feed(
            session,
            from_client=False,
            obj={"type": "response.completed", "response": {"output": []}},
            timestamp=2.0,
        )

        assert len(result) == 1
        assert result[0].events[0].direction == "client_to_server"
        assert result[0].events[0].payload == {"type": "client.control", "value": 1}
