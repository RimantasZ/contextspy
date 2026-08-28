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
from copy import deepcopy

from contextspy.analysis.adapters.base import (
    WireFormatAdapter,
    flatten_content,
    reconcile_thinking,
)
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage
from contextspy.analysis.capture import CanonicalResponse, CapturedEvent


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
            msg_block_type = {
                "assistant": BlockType.ASSISTANT_MESSAGE,
                "system": BlockType.SYSTEM_PROMPT,
            }.get(role, BlockType.USER_MESSAGE)

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
                    thinking_text = part.get("thinking", "")
                    if part.get("signature"):
                        attrs["signature"] = part["signature"]
                    if not thinking_text:
                        attrs["hidden"] = True  # thinking.display: "omitted" (default on newest models)
                    blocks.append(Block.make(
                        Direction.INPUT, BlockType.THINKING, thinking_text,
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

        blocks = [b for b in blocks if b is not None]
        usage_raw = resp_body.get("usage", {}) or {}
        usage = self._usage_from_dict(usage_raw)
        reconcile_thinking(blocks, usage)
        return blocks, usage

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
            text = part.get("thinking", "")
            attrs = {"signature": part["signature"]} if part.get("signature") else {}
            if not text:
                attrs["hidden"] = True  # thinking.display: "omitted" (default on newest models)
            return Block.make(Direction.OUTPUT, BlockType.THINKING, text, attrs=attrs)
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
        # No reasoning_tokens: the Messages API bills thinking inside
        # output_tokens and never breaks it out, whatever thinking.display is
        # set to. reconcile_thinking() derives it from the residual instead.
        return Usage(
            input_tokens=input_tokens,
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    # -- streamed response reconstruction ---------------------------------

    def reconstruct_response(
        self, events: list[CapturedEvent], *, transport: str,
    ) -> CanonicalResponse:
        message: dict = {"type": "message", "role": "assistant", "content": []}
        parts_by_index: dict[int, dict] = {}
        saw_stop = False
        error: dict | None = None

        for captured in events:
            if captured.direction != "server_to_client":
                continue
            event = captured.payload
            if not isinstance(event, dict):
                continue
            etype = event.get("type", "")
            if etype == "message_start" and isinstance(event.get("message"), dict):
                message = deepcopy(event["message"])
                for idx, part in enumerate(message.get("content") or []):
                    if isinstance(part, dict):
                        parts_by_index[idx] = deepcopy(part)
            elif etype == "content_block_start":
                idx = int(event.get("index", 0))
                block = event.get("content_block") or {}
                if isinstance(block, dict):
                    parts_by_index[idx] = deepcopy(block)
                    if block.get("type") == "tool_use":
                        parts_by_index[idx].setdefault("input", {})
                        parts_by_index[idx]["_partial_json"] = ""
            elif etype == "content_block_delta":
                idx = int(event.get("index", 0))
                delta = event.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                dtype = delta.get("type", "")
                part = parts_by_index.setdefault(idx, {"type": "text", "text": ""})
                if dtype == "text_delta":
                    part["text"] = part.get("text", "") + delta.get("text", "")
                elif dtype == "thinking_delta":
                    part.setdefault("type", "thinking")
                    part["thinking"] = part.get("thinking", "") + delta.get("thinking", "")
                elif dtype == "signature_delta":
                    part["signature"] = part.get("signature", "") + delta.get("signature", "")
                elif dtype == "input_json_delta":
                    part["_partial_json"] = part.get("_partial_json", "") + delta.get("partial_json", "")
                else:
                    # Preserve provider extensions on the reconstructed block as
                    # well as in the complete event log.
                    part.setdefault("_stream_deltas", []).append(deepcopy(delta))
            elif etype == "message_delta":
                delta = event.get("delta") or {}
                if isinstance(delta, dict):
                    message.update(deepcopy(delta))
                usage = event.get("usage") or {}
                if isinstance(usage, dict):
                    merged_usage = dict(message.get("usage") or {})
                    merged_usage.update(deepcopy(usage))
                    message["usage"] = merged_usage
            elif etype == "message_stop":
                saw_stop = True
            elif etype == "error":
                error = deepcopy(event)
                message["error"] = deepcopy(event.get("error", event))
                saw_stop = True

        content: list[dict] = []
        for idx in sorted(parts_by_index):
            part = parts_by_index[idx]
            partial = part.pop("_partial_json", None)
            if partial is not None:
                try:
                    part["input"] = json.loads(partial) if partial else part.get("input", {})
                except json.JSONDecodeError:
                    part["input"] = partial
                    part["input_json_incomplete"] = True
            content.append(part)
        message["content"] = content

        return CanonicalResponse(
            payload=message,
            transport=transport,
            events=events,
            reconstructed=True,
            complete=saw_stop or any(event.done for event in events),
            error=error,
        )
