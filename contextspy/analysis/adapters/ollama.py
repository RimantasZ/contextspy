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
"""Ollama native wire format (/api/chat, /api/generate).

No tool-call or system-field concept beyond plain role-based messages.
Streaming responses are newline-delimited JSON (not SSE) — each line is a
full JSON object, not "data: "-prefixed.
"""
from __future__ import annotations

from copy import deepcopy

from contextspy.analysis.adapters.base import (
    WireFormatAdapter,
    flatten_content,
    reconcile_thinking,
)
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage
from contextspy.analysis.capture import CanonicalResponse, CapturedEvent

_BLOCK_TYPE_FOR_ROLE = {
    "system": BlockType.SYSTEM_PROMPT,
    "assistant": BlockType.ASSISTANT_MESSAGE,
}


class OllamaAdapter(WireFormatAdapter):
    format_id = "ollama"
    endpoint_patterns = ("/api/chat", "/api/generate")
    stream_format = "ndjson"

    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        blocks: list[Block] = []
        for i, msg in enumerate(req_body.get("messages", [])):
            role = msg.get("role", "user")
            content = flatten_content(msg.get("content", ""))
            if not content:
                continue
            block_type = _BLOCK_TYPE_FOR_ROLE.get(role, BlockType.USER_MESSAGE)
            blocks.append(Block.make(Direction.INPUT, block_type, content, message_index=i))
        return blocks, {}

    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        blocks: list[Block] = []
        message = resp_body.get("message") or {}
        thinking = flatten_content(message.get("thinking", ""))
        if thinking:
            blocks.append(Block.make(Direction.OUTPUT, BlockType.THINKING, thinking))
        content = flatten_content(message.get("content", ""))
        if content:
            blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, content))
        usage = Usage(
            input_tokens=resp_body.get("prompt_eval_count"),
            output_tokens=resp_body.get("eval_count"),
        )
        reconcile_thinking(blocks, usage)
        return blocks, usage

    def reconstruct_response(
        self, events: list[CapturedEvent], *, transport: str,
    ) -> CanonicalResponse:
        response: dict = {}
        message: dict = {"role": "assistant", "content": ""}
        generated = ""
        saw_done = False

        for captured in events:
            if captured.direction != "server_to_client":
                continue
            event = captured.payload
            if not isinstance(event, dict):
                continue
            for key, value in event.items():
                if key not in ("message", "response") and value is not None:
                    response[key] = deepcopy(value)
            chunk_message = event.get("message") or {}
            if isinstance(chunk_message, dict):
                for key, value in chunk_message.items():
                    if key in ("content", "thinking") and isinstance(value, str):
                        message[key] = message.get(key, "") + value
                    elif key == "tool_calls" and isinstance(value, list):
                        message.setdefault("tool_calls", []).extend(deepcopy(value))
                    elif value is not None:
                        message[key] = deepcopy(value)
            if isinstance(event.get("response"), str):
                generated += event["response"]
            saw_done = saw_done or event.get("done") is True

        if message.get("content") or message.get("thinking") or message.get("tool_calls"):
            response["message"] = message
        if generated:
            response["response"] = generated
            response.setdefault("message", {"role": "assistant", "content": generated})
        return CanonicalResponse(
            payload=response, transport=transport, events=events,
            reconstructed=True, complete=saw_done,
        )
