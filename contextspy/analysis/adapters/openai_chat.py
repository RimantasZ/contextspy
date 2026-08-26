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
from copy import deepcopy

from contextspy.analysis.adapters.base import (
    WireFormatAdapter,
    flatten_content,
    reconcile_thinking,
)
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage
from contextspy.analysis.capture import CanonicalResponse, CapturedEvent


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
        for list_index, choice in enumerate(choices):
            choice_index = int(choice.get("index", list_index))
            msg = choice.get("message") or choice.get("delta") or {}
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            if reasoning:
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.THINKING, reasoning,
                    message_index=choice_index,
                ))
            text = flatten_content(msg.get("content", ""))
            if text:
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, text,
                    message_index=choice_index,
                ))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name") or ""
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.TOOL_CALL, fn.get("arguments", ""),
                    message_index=choice_index, tool_name=name, tool_call_id=tc.get("id"),
                ))
            function_call = msg.get("function_call") or {}
            if isinstance(function_call, dict) and function_call:
                blocks.append(Block.make(
                    Direction.OUTPUT,
                    BlockType.TOOL_CALL,
                    function_call.get("arguments", ""),
                    message_index=choice_index,
                    tool_name=function_call.get("name", ""),
                ))

        usage = resp_body.get("usage", {}) or {}
        details = usage.get("completion_tokens_details") or {}
        usage_obj = Usage(
            input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
            output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
        )
        reconcile_thinking(blocks, usage_obj)
        return blocks, usage_obj

    # -- streamed response reconstruction -------------------------------------

    def reconstruct_response(
        self, events: list[CapturedEvent], *, transport: str,
    ) -> CanonicalResponse:
        response: dict = {"object": "chat.completion", "choices": []}
        choices: dict[int, dict] = {}

        for captured in events:
            if captured.direction != "server_to_client":
                continue
            event = captured.payload
            if not isinstance(event, dict):
                continue
            for key, value in event.items():
                if key not in ("choices", "usage") and value is not None:
                    response[key] = deepcopy(value)
            if isinstance(event.get("usage"), dict):
                response["usage"] = deepcopy(event["usage"])

            for chunk_choice in event.get("choices") or []:
                if not isinstance(chunk_choice, dict):
                    continue
                idx = int(chunk_choice.get("index", 0))
                choice = choices.setdefault(idx, {"index": idx, "message": {}})
                delta = chunk_choice.get("delta") or chunk_choice.get("message") or {}
                message = choice["message"]
                if isinstance(delta, dict):
                    for key, value in delta.items():
                        if value is None:
                            continue
                        if key in ("content", "reasoning_content", "reasoning", "refusal") and isinstance(value, str):
                            message[key] = message.get(key, "") + value
                        elif key == "tool_calls" and isinstance(value, list):
                            tool_calls = message.setdefault("tool_calls", [])
                            for tc in value:
                                if not isinstance(tc, dict):
                                    continue
                                tc_idx = int(tc.get("index", len(tool_calls)))
                                while len(tool_calls) <= tc_idx:
                                    tool_calls.append({"index": len(tool_calls), "function": {"arguments": ""}})
                                target = tool_calls[tc_idx]
                                for tc_key, tc_value in tc.items():
                                    if tc_key == "function" and isinstance(tc_value, dict):
                                        fn = target.setdefault("function", {})
                                        for fn_key, fn_value in tc_value.items():
                                            if fn_key == "arguments" and isinstance(fn_value, str):
                                                fn[fn_key] = fn.get(fn_key, "") + fn_value
                                            elif fn_value is not None:
                                                fn[fn_key] = deepcopy(fn_value)
                                    elif tc_key != "index" and tc_value is not None:
                                        target[tc_key] = deepcopy(tc_value)
                        elif key == "function_call" and isinstance(value, dict):
                            function_call = message.setdefault(
                                "function_call", {"arguments": ""},
                            )
                            for fn_key, fn_value in value.items():
                                if fn_key == "arguments" and isinstance(fn_value, str):
                                    function_call[fn_key] = function_call.get(fn_key, "") + fn_value
                                elif fn_value is not None:
                                    function_call[fn_key] = deepcopy(fn_value)
                        elif isinstance(value, list) and isinstance(message.get(key), list):
                            message[key].extend(deepcopy(value))
                        else:
                            message[key] = deepcopy(value)
                for key, value in chunk_choice.items():
                    if key not in ("delta", "message") and value is not None:
                        choice[key] = deepcopy(value)

        response["choices"] = [choices[idx] for idx in sorted(choices)]
        if response.get("object") == "chat.completion.chunk":
            response["object"] = "chat.completion"
        complete = any(event.done for event in events) or any(
            choice.get("finish_reason") is not None for choice in response["choices"]
        )
        return CanonicalResponse(
            payload=response, transport=transport, events=events,
            reconstructed=True, complete=complete,
        )
