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
"""Anthropic Messages API adapter (/v1/messages).

Also used for gateways that relay other agents' traffic in Anthropic's wire
format (e.g. GitHub Copilot -> Claude, opencode's zen gateway).
"""
from __future__ import annotations

import json

from contextspy.analysis.adapters.base import WireFormatAdapter, flatten_content
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage


def _cache_attrs(part: dict) -> dict:
    cc = part.get("cache_control")
    return {"cache_control": cc} if cc else {}


class AnthropicAdapter(WireFormatAdapter):
    format_id = "anthropic"
    endpoint_patterns = ("/messages",)

    # -- request -------------------------------------------------------

    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        blocks: list[Block] = []
        tool_call_map: dict[str, str] = {}

        system_text = req_body.get("system", "")
        if system_text:
            text = flatten_content(system_text) if isinstance(system_text, list) else system_text
            attrs = {}
            if isinstance(system_text, list):
                for part in system_text:
                    if isinstance(part, dict) and part.get("cache_control"):
                        attrs["cache_control"] = part["cache_control"]
                        break
            if text:
                blocks.append(Block.make(
                    Direction.INPUT, BlockType.SYSTEM_PROMPT, text,
                    message_index=-1, attrs=attrs,
                ))

        for tool in req_body.get("tools", []) or []:
            name = tool.get("name") or (tool.get("function") or {}).get("name") or "unknown"
            blocks.append(Block.make(
                Direction.INPUT, BlockType.TOOL_DEFINITION, json.dumps(tool),
                tool_name=name, attrs=_cache_attrs(tool),
            ))

        raw_messages = req_body.get("messages", [])
        is_last_assistant = bool(raw_messages) and raw_messages[-1].get("role") == "assistant"

        # First pass: build tool_call_map from tool_use blocks (always precede
        # their tool_result in conversation order) and emit content-part blocks.
        pending_tool_results: list[Block] = []
        for i, msg in enumerate(raw_messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            is_prefill = is_prefill_msg = (i == len(raw_messages) - 1 and is_last_assistant)
            msg_block_type = BlockType.ASSISTANT_MESSAGE if role == "assistant" else BlockType.USER_MESSAGE

            if isinstance(content, str):
                if content:
                    blocks.append(Block.make(
                        Direction.INPUT, msg_block_type, content, message_index=i,
                        attrs={"is_prefill": True} if is_prefill else {},
                    ))
                continue

            if not isinstance(content, list):
                continue

            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                attrs = _cache_attrs(part)
                if is_prefill:
                    attrs["is_prefill"] = True

                if ptype == "text":
                    text = part.get("text", "")
                    if text:
                        blocks.append(Block.make(
                            Direction.INPUT, msg_block_type, text,
                            message_index=i, attrs=attrs,
                        ))
                elif ptype == "tool_use":
                    call_id = part.get("id")
                    name = part.get("name", "")
                    if call_id and name:
                        tool_call_map[call_id] = name
                    blocks.append(Block.make(
                        Direction.INPUT, BlockType.TOOL_CALL,
                        json.dumps(part.get("input", {})),
                        message_index=i, tool_name=name, tool_call_id=call_id, attrs=attrs,
                    ))
                elif ptype == "tool_result":
                    b = Block.make(
                        Direction.INPUT, BlockType.TOOL_RESULT,
                        flatten_content(part.get("content", "")),
                        message_index=i, tool_call_id=part.get("tool_use_id"), attrs=attrs,
                    )
                    blocks.append(b)
                    pending_tool_results.append(b)
                elif ptype == "thinking":
                    if part.get("signature"):
                        attrs["signature"] = part["signature"]
                    blocks.append(Block.make(
                        Direction.INPUT, BlockType.THINKING, part.get("thinking", ""),
                        message_index=i, attrs=attrs,
                    ))
                elif ptype == "redacted_thinking":
                    attrs["redacted"] = True
                    blocks.append(Block.make(
                        Direction.INPUT, BlockType.THINKING, "",
                        message_index=i, attrs=attrs,
                    ))
                else:
                    blocks.append(Block.make(
                        Direction.INPUT, BlockType.OTHER, json.dumps(part),
                        message_index=i, attrs={**attrs, "content_type": ptype},
                    ))

        # Resolve tool_result -> tool_name now that tool_call_map is complete.
        for b in pending_tool_results:
            if b.tool_call_id and b.tool_call_id in tool_call_map:
                b.tool_name = tool_call_map[b.tool_call_id]

        return blocks, tool_call_map

    # -- response --------------------------------------------------------

    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        blocks: list[Block] = []
        content = resp_body.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}] if content else []
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            blocks.append(self._output_block_from_part(part))

        usage_raw = resp_body.get("usage", {}) or {}
        usage = self._usage_from_dict(usage_raw)
        return [b for b in blocks if b is not None], usage

    def _output_block_from_part(self, part: dict) -> Block | None:
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text", "")
            return Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, text) if text else None
        if ptype == "tool_use":
            name = part.get("name", "")
            return Block.make(
                Direction.OUTPUT, BlockType.TOOL_CALL, json.dumps(part.get("input", {})),
                tool_name=name, tool_call_id=part.get("id"),
            )
        if ptype == "thinking":
            attrs = {"signature": part["signature"]} if part.get("signature") else {}
            return Block.make(Direction.OUTPUT, BlockType.THINKING, part.get("thinking", ""), attrs=attrs)
        if ptype == "redacted_thinking":
            return Block.make(Direction.OUTPUT, BlockType.THINKING, "", attrs={"redacted": True})
        return Block.make(Direction.OUTPUT, BlockType.OTHER, json.dumps(part), attrs={"content_type": ptype})

    @staticmethod
    def _usage_from_dict(usage: dict) -> Usage:
        raw_read = usage.get("cache_read_input_tokens")
        raw_creation = usage.get("cache_creation_input_tokens")
        cache_read = raw_read if raw_read is not None else None
        cache_creation = raw_creation if raw_creation is not None else None
        billed = usage.get("input_tokens") or 0
        input_tokens = billed + (cache_read or 0) + (cache_creation or 0) if usage else None
        return Usage(
            input_tokens=input_tokens,
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    # -- SSE ---------------------------------------------------------------

    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        text_data = raw.decode("utf-8", errors="replace")
        input_tokens: int | None = None
        output_tokens: int | None = None
        cache_read: int | None = None
        cache_creation: int | None = None
        parts_by_index: dict[int, dict] = {}

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
            etype = event.get("type", "")

            if etype == "message_start":
                usage = event.get("message", {}).get("usage", {})
                if "input_tokens" in usage:
                    input_tokens = usage["input_tokens"]
                if "cache_read_input_tokens" in usage:
                    cache_read = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    cache_creation = usage["cache_creation_input_tokens"]

            elif etype == "content_block_start":
                idx = event.get("index", 0)
                cb = event.get("content_block", {})
                entry = parts_by_index.setdefault(idx, {})
                entry["type"] = cb.get("type", "text")
                if entry["type"] == "text":
                    entry["text"] = cb.get("text", "")
                elif entry["type"] == "thinking":
                    entry["thinking"] = cb.get("thinking", "")
                elif entry["type"] == "redacted_thinking":
                    entry["data"] = cb.get("data", "")
                elif entry["type"] == "tool_use":
                    entry["name"] = cb.get("name", "")
                    entry["id"] = cb.get("id", "")
                    entry["partial_json"] = ""

            elif etype == "content_block_delta":
                idx = event.get("index", 0)
                entry = parts_by_index.setdefault(idx, {"type": "text", "text": ""})
                delta = event.get("delta", {})
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    entry["text"] = entry.get("text", "") + delta.get("text", "")
                elif dtype == "thinking_delta":
                    entry["thinking"] = entry.get("thinking", "") + delta.get("thinking", "")
                elif dtype == "signature_delta":
                    entry["signature"] = entry.get("signature", "") + delta.get("signature", "")
                elif dtype == "input_json_delta":
                    entry["partial_json"] = entry.get("partial_json", "") + delta.get("partial_json", "")

            elif etype == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    output_tokens = usage["output_tokens"]
                # Some providers (e.g. Copilot via Bedrock) report all token counts
                # in message_delta rather than message_start — capture as fallback.
                if "input_tokens" in usage and input_tokens is None:
                    input_tokens = usage["input_tokens"]
                if "cache_read_input_tokens" in usage and cache_read is None:
                    cache_read = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage and cache_creation is None:
                    cache_creation = usage["cache_creation_input_tokens"]

        blocks: list[Block] = []
        for idx in sorted(parts_by_index):
            entry = parts_by_index[idx]
            btype = entry.get("type", "text")
            if btype == "text":
                text = entry.get("text", "")
                if text:
                    blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, text, message_index=idx))
            elif btype == "thinking":
                attrs = {"signature": entry["signature"]} if entry.get("signature") else {}
                blocks.append(Block.make(Direction.OUTPUT, BlockType.THINKING, entry.get("thinking", ""),
                                          message_index=idx, attrs=attrs))
            elif btype == "redacted_thinking":
                blocks.append(Block.make(Direction.OUTPUT, BlockType.THINKING, "",
                                          message_index=idx, attrs={"redacted": True}))
            elif btype == "tool_use":
                name = entry.get("name", "")
                args = entry.get("partial_json", "")
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.TOOL_CALL, args,
                    message_index=idx, tool_name=name, tool_call_id=entry.get("id"),
                ))

        billed = input_tokens or 0
        total_input = billed + (cache_read or 0) + (cache_creation or 0) if (
            input_tokens is not None or cache_read is not None or cache_creation is not None
        ) else None
        usage = Usage(
            input_tokens=total_input,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )
        return blocks, usage
