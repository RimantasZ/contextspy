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
"""OpenAI Chat Completions wire format (/chat/completions, /completions).

Also covers any OpenAI-compatible server (Ollama's /v1/chat/completions,
llama-server, vLLM) and gateways relaying to OpenAI-format models
(Copilot, opencode zen).
"""
from __future__ import annotations

import json

from contextspy.analysis.adapters.base import WireFormatAdapter, flatten_content
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage


class OpenAIChatAdapter(WireFormatAdapter):
    format_id = "openai_chat"
    endpoint_patterns = ("/chat/completions", "/completions")

    # -- request -----------------------------------------------------------

    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        blocks: list[Block] = []
        tool_call_map: dict[str, str] = {}

        tools = req_body.get("tools") or req_body.get("functions") or []
        for tool in tools:
            name = tool.get("name") or (tool.get("function") or {}).get("name") or "unknown"
            blocks.append(Block.make(
                Direction.INPUT, BlockType.TOOL_DEFINITION, json.dumps(tool), tool_name=name,
            ))

        raw_messages = req_body.get("messages", [])
        pending_tool_results: list[Block] = []

        for i, msg in enumerate(raw_messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            is_tool_result = role == "tool" or bool(msg.get("tool_call_id"))

            if is_tool_result:
                b = Block.make(
                    Direction.INPUT, BlockType.TOOL_RESULT, flatten_content(content),
                    message_index=i, tool_call_id=msg.get("tool_call_id"),
                )
                blocks.append(b)
                pending_tool_results.append(b)
                continue

            if role == "system":
                text = flatten_content(content)
                if text:
                    blocks.append(Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, text, message_index=i))
                continue

            msg_block_type = BlockType.ASSISTANT_MESSAGE if role == "assistant" else BlockType.USER_MESSAGE

            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and part.get("text"):
                        blocks.append(Block.make(Direction.INPUT, msg_block_type, part["text"], message_index=i))
                    elif part.get("type") not in ("text",):
                        blocks.append(Block.make(
                            Direction.INPUT, BlockType.OTHER, json.dumps(part),
                            message_index=i, attrs={"content_type": part.get("type")},
                        ))
            elif isinstance(content, str) and content:
                blocks.append(Block.make(Direction.INPUT, msg_block_type, content, message_index=i))

            for tc in msg.get("tool_calls") or []:
                call_id = tc.get("id")
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name")
                if call_id and name:
                    tool_call_map[call_id] = name
                blocks.append(Block.make(
                    Direction.INPUT, BlockType.TOOL_CALL, fn.get("arguments", ""),
                    message_index=i, tool_name=name, tool_call_id=call_id,
                ))

        for b in pending_tool_results:
            if b.tool_call_id and b.tool_call_id in tool_call_map:
                b.tool_name = tool_call_map[b.tool_call_id]

        return blocks, tool_call_map

    # -- response ------------------------------------------------------------

    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        blocks: list[Block] = []
        choices = resp_body.get("choices", [])
        if choices:
            msg = choices[0].get("message") or choices[0].get("delta") or {}
            text = flatten_content(msg.get("content", ""))
            if text:
                blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, text))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name") or ""
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.TOOL_CALL, fn.get("arguments", ""),
                    tool_name=name, tool_call_id=tc.get("id"),
                ))

        usage = resp_body.get("usage", {}) or {}
        details = usage.get("completion_tokens_details") or {}
        return blocks, Usage(
            input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
            output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
        )

    # -- SSE -------------------------------------------------------------------

    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        text_data = raw.decode("utf-8", errors="replace")
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        reasoning_tokens: int | None = None
        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}

        for line in text_data.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            for choice in event.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    entry = tool_calls_acc.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]

            usage = event.get("usage") or {}
            if usage.get("prompt_tokens") is not None:
                prompt_tokens = usage["prompt_tokens"]
            if usage.get("completion_tokens") is not None:
                completion_tokens = usage["completion_tokens"]
            details = usage.get("completion_tokens_details") or {}
            if details.get("reasoning_tokens") is not None:
                reasoning_tokens = details["reasoning_tokens"]

        blocks: list[Block] = []
        response_content = "".join(content_parts)
        if response_content:
            blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, response_content))
        for idx in sorted(tool_calls_acc):
            entry = tool_calls_acc[idx]
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.TOOL_CALL, entry["arguments"],
                tool_name=entry["name"] or "", tool_call_id=entry["id"],
            ))

        usage_obj = Usage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
        ) if (prompt_tokens is not None or completion_tokens is not None) else Usage()
        return blocks, usage_obj
