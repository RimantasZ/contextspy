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
"""OpenAI Responses API wire format (/responses).

Key differences from Chat Completions:
  - input (not messages), instructions (not a system message)
  - function_call / function_call_output / reasoning items alongside role-based ones
  - output (not choices), output_text content parts
  - usage.input_tokens / output_tokens, usage.output_tokens_details.reasoning_tokens

Reasoning content is usually hidden by the provider (summary omitted unless
requested): when no "reasoning" item is present in the output but
usage reports reasoning_tokens > 0, a synthetic hidden THINKING block is
emitted so those tokens are still visible in the breakdown.
"""
from __future__ import annotations

import json

from contextspy.analysis.adapters.base import WireFormatAdapter
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage


def _reasoning_summary_text(item: dict) -> str:
    parts = [s.get("text", "") for s in item.get("summary", []) or [] if isinstance(s, dict)]
    return "\n".join(p for p in parts if p)


class OpenAIResponsesAdapter(WireFormatAdapter):
    format_id = "openai_responses"
    endpoint_patterns = ("/responses",)

    # -- request -------------------------------------------------------

    def parse_request(self, req_body: dict) -> tuple[list[Block], dict[str, str]]:
        blocks: list[Block] = []
        tool_call_map: dict[str, str] = {}
        pending_tool_results: list[Block] = []

        for tool in req_body.get("tools") or []:
            name = tool.get("name") or (tool.get("function") or {}).get("name") or "unknown"
            blocks.append(Block.make(
                Direction.INPUT, BlockType.TOOL_DEFINITION, json.dumps(tool), tool_name=name,
            ))

        instructions = req_body.get("instructions", "")
        if instructions:
            blocks.append(Block.make(Direction.INPUT, BlockType.SYSTEM_PROMPT, instructions, message_index=-1))

        block_type_for_role = {
            "user": BlockType.USER_MESSAGE,
            "assistant": BlockType.ASSISTANT_MESSAGE,
            "system": BlockType.SYSTEM_PROMPT,
        }

        for i, item in enumerate(req_body.get("input", [])):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            role = item.get("role", "")

            if item_type == "function_call_output":
                call_id = item.get("call_id")
                output = item.get("output", "")
                text = output if isinstance(output, str) else json.dumps(output)
                b = Block.make(Direction.INPUT, BlockType.TOOL_RESULT, text, message_index=i, tool_call_id=call_id)
                blocks.append(b)
                pending_tool_results.append(b)
            elif item_type == "function_call":
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name", "")
                args = item.get("arguments", "")
                if call_id and name:
                    tool_call_map[call_id] = name
                blocks.append(Block.make(
                    Direction.INPUT, BlockType.TOOL_CALL, args,
                    message_index=i, tool_name=name, tool_call_id=call_id,
                ))
            elif item_type == "reasoning":
                text = _reasoning_summary_text(item)
                attrs = {} if text else {"hidden": True}
                blocks.append(Block.make(Direction.INPUT, BlockType.THINKING, text, message_index=i, attrs=attrs))
            elif role in block_type_for_role:
                block_type = block_type_for_role[role]
                content_raw = item.get("content", "")
                if isinstance(content_raw, list):
                    for part in content_raw:
                        if not isinstance(part, dict):
                            continue
                        ptype = part.get("type")
                        if ptype in ("input_text", "output_text", "text") and part.get("text"):
                            blocks.append(Block.make(Direction.INPUT, block_type, part["text"], message_index=i))
                        elif ptype == "refusal" and part.get("refusal"):
                            blocks.append(Block.make(Direction.INPUT, block_type, part["refusal"],
                                                      message_index=i, attrs={"refusal": True}))
                        else:
                            blocks.append(Block.make(Direction.INPUT, BlockType.OTHER, json.dumps(part),
                                                      message_index=i, attrs={"content_type": ptype}))
                elif isinstance(content_raw, str) and content_raw:
                    blocks.append(Block.make(Direction.INPUT, block_type, content_raw, message_index=i))

        for b in pending_tool_results:
            if b.tool_call_id and b.tool_call_id in tool_call_map:
                b.tool_name = tool_call_map[b.tool_call_id]

        return blocks, tool_call_map

    # -- response --------------------------------------------------------

    def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
        blocks: list[Block] = []
        saw_reasoning_item = False

        for item in resp_body.get("output", []):
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "message":
                for part in item.get("content", []) or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "output_text" and part.get("text"):
                        blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, part["text"]))
                    elif part.get("type") == "refusal" and part.get("refusal"):
                        blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, part["refusal"],
                                                  attrs={"refusal": True}))
            elif itype == "function_call":
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name", "")
                blocks.append(Block.make(
                    Direction.OUTPUT, BlockType.TOOL_CALL, item.get("arguments", ""),
                    tool_name=name, tool_call_id=call_id,
                ))
            elif itype == "reasoning":
                saw_reasoning_item = True
                text = _reasoning_summary_text(item)
                attrs = {} if text else {"hidden": True}
                blocks.append(Block.make(Direction.OUTPUT, BlockType.THINKING, text, attrs=attrs))

        usage_raw = resp_body.get("usage", {}) or {}
        details = usage_raw.get("output_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens")
        if reasoning_tokens and not saw_reasoning_item:
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.THINKING, "",
                attrs={"hidden": True}, token_count=reasoning_tokens,
            ))

        usage = Usage(
            input_tokens=usage_raw.get("input_tokens"),
            output_tokens=usage_raw.get("output_tokens"),
            reasoning_tokens=reasoning_tokens,
        )
        return blocks, usage

    # -- SSE ---------------------------------------------------------------

    def parse_sse(self, raw: bytes) -> tuple[list[Block], Usage]:
        text_data = raw.decode("utf-8", errors="replace")
        input_tokens: int | None = None
        output_tokens: int | None = None
        reasoning_tokens: int | None = None
        text_by_index: dict[int, list[str]] = {}
        fc_by_index: dict[int, dict] = {}
        reasoning_by_index: dict[int, dict] = {}

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

            if etype == "response.output_text.delta":
                idx = event.get("output_index", 0)
                text_by_index.setdefault(idx, []).append(event.get("delta", ""))

            elif etype == "response.function_call_arguments.delta":
                idx = event.get("output_index", 0)
                fc_by_index.setdefault(idx, {"name": "", "call_id": "", "args": []})
                fc_by_index[idx]["args"].append(event.get("delta", ""))

            elif etype == "response.reasoning_summary_text.delta":
                idx = event.get("output_index", 0)
                reasoning_by_index.setdefault(idx, {"summary": []})
                reasoning_by_index[idx]["summary"].append(event.get("delta", ""))

            elif etype == "response.output_item.added":
                item = event.get("item", {})
                idx = event.get("output_index", 0)
                if item.get("type") == "function_call":
                    fc_by_index.setdefault(idx, {"name": "", "call_id": "", "args": []})
                    fc_by_index[idx]["name"] = item.get("name", "")
                    fc_by_index[idx]["call_id"] = item.get("call_id") or item.get("id", "")
                elif item.get("type") == "reasoning":
                    reasoning_by_index.setdefault(idx, {"summary": []})

            elif etype == "response.completed":
                resp_obj = event.get("response", {})
                usage = resp_obj.get("usage", {})
                if input_tokens is None:
                    input_tokens = usage.get("input_tokens")
                if output_tokens is None:
                    output_tokens = usage.get("output_tokens")
                details = usage.get("output_tokens_details") or {}
                if reasoning_tokens is None:
                    reasoning_tokens = details.get("reasoning_tokens")

        blocks: list[Block] = []
        for idx in sorted(text_by_index):
            text = "".join(text_by_index[idx])
            if text:
                blocks.append(Block.make(Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, text, message_index=idx))
        for idx in sorted(fc_by_index):
            fc = fc_by_index[idx]
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.TOOL_CALL, "".join(fc["args"]),
                message_index=idx, tool_name=fc["name"], tool_call_id=fc["call_id"] or None,
            ))
        saw_reasoning = False
        for idx in sorted(reasoning_by_index):
            text = "".join(reasoning_by_index[idx]["summary"])
            saw_reasoning = True
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.THINKING, text,
                message_index=idx, attrs={} if text else {"hidden": True},
            ))
        if reasoning_tokens and not saw_reasoning:
            blocks.append(Block.make(
                Direction.OUTPUT, BlockType.THINKING, "",
                attrs={"hidden": True}, token_count=reasoning_tokens,
            ))

        usage_obj = Usage(
            input_tokens=input_tokens, output_tokens=output_tokens, reasoning_tokens=reasoning_tokens,
        ) if (input_tokens is not None or output_tokens is not None) else Usage()
        return blocks, usage_obj
