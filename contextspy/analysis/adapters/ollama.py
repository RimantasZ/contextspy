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

import json

from contextspy.analysis.adapters.base import WireFormatAdapter, flatten_content
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage

_BLOCK_TYPE_FOR_ROLE = {
    "system": BlockType.SYSTEM_PROMPT,
    "assistant": BlockType.ASSISTANT_MESSAGE,
}


class OllamaAdapter(WireFormatAdapter):
    format_id = "ollama"
    endpoint_patterns = ("/api/chat", "/api/generate")

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
        content = flatten_content((resp_body.get("message") or {}).get("content", ""))
        if content:
            blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, content))
        usage = Usage(
            input_tokens=resp_body.get("prompt_eval_count"),
            output_tokens=resp_body.get("eval_count"),
        )
        return blocks, usage

    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        text_data = raw.decode("utf-8", errors="replace")
        content_parts: list[str] = []
        prompt_eval_count: int | None = None
        eval_count: int | None = None
        for line in text_data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_content = (event.get("message") or {}).get("content")
            if msg_content:
                content_parts.append(msg_content)
            if event.get("prompt_eval_count") is not None:
                prompt_eval_count = event["prompt_eval_count"]
            if event.get("eval_count") is not None:
                eval_count = event["eval_count"]

        blocks: list[Block] = []
        content = "".join(content_parts)
        if content:
            blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, content))
        return blocks, Usage(input_tokens=prompt_eval_count, output_tokens=eval_count)
