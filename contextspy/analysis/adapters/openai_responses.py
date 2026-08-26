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
requested), and even when a summary is returned it is far shorter than the
reasoning actually billed. Either way the reported reasoning_tokens is the
figure that counts — reconcile_thinking() puts it on the THINKING block
(synthesising one when the output carried no reasoning item at all).
"""
from __future__ import annotations

import json
from copy import deepcopy

from contextspy.analysis.adapters.base import (
    WireFormatAdapter,
    reconcile_thinking,
)
from contextspy.analysis.blocks import Block, BlockType, Direction, Usage
from contextspy.analysis.capture import CanonicalResponse, CapturedEvent


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
                text = _reasoning_summary_text(item)
                blocks.append(Block.make(Direction.OUTPUT, BlockType.THINKING, text))

        usage_raw = resp_body.get("usage", {}) or {}
        details = usage_raw.get("output_tokens_details") or {}
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens"),
            output_tokens=usage_raw.get("output_tokens"),
            reasoning_tokens=details.get("reasoning_tokens"),
        )
        reconcile_thinking(blocks, usage)
        return blocks, usage

    # -- streamed response reconstruction ---------------------------------

    def reconstruct_response(
        self, events: list[CapturedEvent], *, transport: str,
    ) -> CanonicalResponse:
        response: dict = {"object": "response", "output": []}
        output_by_index: dict[int, dict] = {}
        completed_snapshot: dict | None = None
        error: dict | None = None
        saw_terminal_event = False

        def item_at(index: int, item_type: str = "message") -> dict:
            item = output_by_index.setdefault(index, {"type": item_type})
            item.setdefault("type", item_type)
            return item

        def content_part(item: dict, index: int, part_type: str) -> dict:
            content = item.setdefault("content", [])
            while len(content) <= index:
                content.append({"type": "output_text", "text": ""})
            part = content[index]
            if not isinstance(part, dict):
                part = {"type": part_type}
                content[index] = part
            part.setdefault("type", part_type)
            return part

        for captured in events:
            if captured.direction != "server_to_client":
                continue
            event = captured.payload
            if not isinstance(event, dict):
                continue
            etype = event.get("type", "")
            if etype in ("response.created", "response.in_progress"):
                snapshot = event.get("response")
                if isinstance(snapshot, dict):
                    response.update(deepcopy(snapshot))
                    for idx, item in enumerate(snapshot.get("output") or []):
                        if isinstance(item, dict):
                            output_by_index[idx] = deepcopy(item)
            elif etype in ("response.output_item.added", "response.output_item.done"):
                idx = int(event.get("output_index", 0))
                item = event.get("item")
                if isinstance(item, dict):
                    if etype.endswith(".done") or idx not in output_by_index:
                        output_by_index[idx] = deepcopy(item)
                    else:
                        output_by_index[idx].update(deepcopy(item))
            elif etype in ("response.content_part.added", "response.content_part.done"):
                out_idx = int(event.get("output_index", 0))
                content_idx = int(event.get("content_index", 0))
                part = event.get("part")
                item = item_at(out_idx)
                if isinstance(part, dict):
                    target = content_part(item, content_idx, part.get("type", "output_text"))
                    target.update(deepcopy(part))
            elif etype in ("response.output_text.delta", "response.output_text.done"):
                out_idx = int(event.get("output_index", 0))
                content_idx = int(event.get("content_index", 0))
                part = content_part(item_at(out_idx), content_idx, "output_text")
                if etype.endswith(".delta"):
                    part["text"] = part.get("text", "") + event.get("delta", "")
                elif event.get("text") is not None:
                    part["text"] = event["text"]
                for key in ("annotations", "logprobs"):
                    if event.get(key) is not None:
                        part[key] = deepcopy(event[key])
            elif etype in ("response.refusal.delta", "response.refusal.done"):
                out_idx = int(event.get("output_index", 0))
                content_idx = int(event.get("content_index", 0))
                part = content_part(item_at(out_idx), content_idx, "refusal")
                if etype.endswith(".delta"):
                    part["refusal"] = part.get("refusal", "") + event.get("delta", "")
                elif event.get("refusal") is not None:
                    part["refusal"] = event["refusal"]
            elif etype in (
                "response.function_call_arguments.delta",
                "response.function_call_arguments.done",
            ):
                idx = int(event.get("output_index", 0))
                item = item_at(idx, "function_call")
                item["type"] = "function_call"
                if etype.endswith(".delta"):
                    item["arguments"] = item.get("arguments", "") + event.get("delta", "")
                elif event.get("arguments") is not None:
                    item["arguments"] = event["arguments"]
                for key in ("name", "call_id", "item_id"):
                    if event.get(key) is not None:
                        item["id" if key == "item_id" else key] = event[key]
            elif etype in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_summary_text.done",
            ):
                idx = int(event.get("output_index", 0))
                summary_idx = int(event.get("summary_index", 0))
                item = item_at(idx, "reasoning")
                item["type"] = "reasoning"
                summary = item.setdefault("summary", [])
                while len(summary) <= summary_idx:
                    summary.append({"type": "summary_text", "text": ""})
                part = summary[summary_idx]
                if etype.endswith(".delta"):
                    part["text"] = part.get("text", "") + event.get("delta", "")
                elif event.get("text") is not None:
                    part["text"] = event["text"]
            elif etype in ("response.completed", "response.failed", "response.incomplete"):
                snapshot = event.get("response")
                if isinstance(snapshot, dict):
                    completed_snapshot = deepcopy(snapshot)
                saw_terminal_event = True
                if etype == "response.failed" or (
                    isinstance(snapshot, dict) and snapshot.get("error") is not None
                ):
                    error = deepcopy(event)
            elif etype == "error":
                error = deepcopy(event)
                saw_terminal_event = True

        if completed_snapshot is not None:
            final = deepcopy(response)
            final.update(completed_snapshot)
            snapshot_output = completed_snapshot.get("output")
            if isinstance(snapshot_output, list):
                output_by_index = {
                    idx: deepcopy(item) for idx, item in enumerate(snapshot_output)
                    if isinstance(item, dict)
                }
        else:
            final = response
        final["output"] = [output_by_index[idx] for idx in sorted(output_by_index)]
        if error is not None:
            final.setdefault("error", deepcopy(error.get("error", error)))
            final.setdefault("status", "failed")

        complete = saw_terminal_event or any(event.done for event in events)
        return CanonicalResponse(
            payload=final, transport=transport, events=events,
            reconstructed=True, complete=complete, error=error,
        )
