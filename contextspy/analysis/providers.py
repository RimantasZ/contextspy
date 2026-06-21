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
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedMessage:
    role: str
    content: str  # normalised to string
    tool_call_id: str | None = None
    is_tool_result: bool = False
    is_tool_definition: bool = False
    is_assistant_prefill: bool = False


@dataclass
class ParsedRequest:
    model: str | None
    messages: list[ParsedMessage]
    tool_definitions_text: str  # serialised JSON of tools array
    provider_input_tokens: int | None
    provider_output_tokens: int | None
    response_text: str
    raw_endpoint: str = ""
    # Maps tool_call_id → tool_name for accurate result attribution
    tool_call_map: dict[str, str] = field(default_factory=dict)
    # Anthropic prompt-cache breakdown (None for non-Anthropic providers)
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    # Thinking / reasoning token tracking
    thinking_text: str = ""
    provider_thinking_tokens: int | None = None


def _content_to_str(content: Any) -> str:
    """Normalise message content to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        parts.extend(
                            b.get("text", "") for b in inner if isinstance(b, dict)
                        )
                    else:
                        parts.append(str(inner))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content) if content is not None else ""


# ---------------------------------------------------------------------------
# OpenAI / Copilot parser
# ---------------------------------------------------------------------------

def parse_openai(req_body: dict, resp_body: dict) -> ParsedRequest:
    model = req_body.get("model")
    raw_messages = req_body.get("messages", [])
    tools = req_body.get("tools") or req_body.get("functions") or []
    tool_defs_text = json.dumps(tools) if tools else ""

    messages: list[ParsedMessage] = []
    tool_call_map: dict[str, str] = {}

    for msg in raw_messages:
        role = msg.get("role", "user")
        content = _content_to_str(msg.get("content", ""))
        is_tool_result = role == "tool" or bool(msg.get("tool_call_id"))
        tool_call_id = msg.get("tool_call_id")

        # Build call_id → tool_name map from assistant tool_calls, and include
        # the serialised tool_calls JSON in content so those tokens are counted.
        # (Assistant messages that only contain tool_calls have content=null in
        # the wire format, but the provider bills the full function-call JSON.)
        tool_calls_list = msg.get("tool_calls") or []
        for tc in tool_calls_list:
            call_id = tc.get("id")
            name = (tc.get("function") or {}).get("name") or tc.get("name")
            if call_id and name:
                tool_call_map[call_id] = name
        if tool_calls_list and role == "assistant":
            tc_text = json.dumps(tool_calls_list)
            content = (content + "\n" + tc_text).strip() if content else tc_text

        messages.append(
            ParsedMessage(
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                is_tool_result=is_tool_result,
            )
        )

    # Provider-reported usage
    provider_input = None
    provider_output = None
    provider_thinking = None
    response_text = ""
    usage = resp_body.get("usage", {})
    if usage:
        provider_input = usage.get("prompt_tokens") or usage.get("input_tokens")
        provider_output = usage.get("completion_tokens") or usage.get("output_tokens")
        details = usage.get("completion_tokens_details") or {}
        provider_thinking = details.get("reasoning_tokens") or None
    choices = resp_body.get("choices", [])
    if choices:
        msg = choices[0].get("message") or choices[0].get("delta") or {}
        response_text = _content_to_str(msg.get("content", ""))
        # Also serialise tool-call arguments so output tokens are counted correctly
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            tc_text = json.dumps(tool_calls)
            response_text = (response_text + "\n" + tc_text).strip() if response_text else tc_text

    return ParsedRequest(
        model=model,
        messages=messages,
        tool_definitions_text=tool_defs_text,
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        response_text=response_text,
        tool_call_map=tool_call_map,
        provider_thinking_tokens=provider_thinking,
    )


# ---------------------------------------------------------------------------
# Anthropic parser
# ---------------------------------------------------------------------------

def parse_anthropic(req_body: dict, resp_body: dict) -> ParsedRequest:
    model = req_body.get("model")
    raw_messages = req_body.get("messages", [])
    system_text = req_body.get("system", "")
    tools = req_body.get("tools", [])
    tool_defs_text = json.dumps(tools) if tools else ""

    messages: list[ParsedMessage] = []
    tool_call_map: dict[str, str] = {}

    # Inject system as a synthetic message
    if system_text:
        if isinstance(system_text, list):
            system_text = _content_to_str(system_text)
        messages.append(ParsedMessage(role="system", content=system_text))

    for msg in raw_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Build call_id → tool_name from assistant tool_use blocks
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    call_id = block.get("id")
                    name = block.get("name")
                    if call_id and name:
                        tool_call_map[call_id] = name

        # Detect tool_result blocks and extract tool_use_id
        is_tool_result = False
        tool_call_id = None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    is_tool_result = True
                    # Take the first tool_use_id found
                    if tool_call_id is None:
                        tool_call_id = block.get("tool_use_id")

        messages.append(
            ParsedMessage(
                role=role,
                content=_content_to_str(content),
                tool_call_id=tool_call_id,
                is_tool_result=is_tool_result,
            )
        )

    # Detect assistant prefill (trailing assistant turn with no stop reason)
    if messages and messages[-1].role == "assistant":
        messages[-1].is_assistant_prefill = True

    # Provider usage
    provider_input = None
    provider_output = None
    cache_read: int | None = None
    cache_creation: int | None = None
    response_text = ""
    usage = resp_body.get("usage", {})
    if usage:
        provider_output = usage.get("output_tokens")
        # cache_read/creation may be 0 or absent on non-cache requests
        raw_read = usage.get("cache_read_input_tokens")
        raw_creation = usage.get("cache_creation_input_tokens")
        cache_read = raw_read if raw_read is not None else None
        cache_creation = raw_creation if raw_creation is not None else None
        # provider_input_tokens = full context (billed + cached)
        billed = usage.get("input_tokens") or 0
        provider_input = billed + (cache_read or 0) + (cache_creation or 0)
    resp_content = resp_body.get("content", [])
    thinking_parts: list[str] = []
    if isinstance(resp_content, list):
        non_thinking: list[Any] = []
        for block in resp_content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                text = block.get("thinking", "")
                if text:
                    thinking_parts.append(text)
            else:
                non_thinking.append(block)
        response_text = _content_to_str(non_thinking)
    elif isinstance(resp_content, str):
        response_text = resp_content

    return ParsedRequest(
        model=model,
        messages=messages,
        tool_definitions_text=tool_defs_text,
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        response_text=response_text,
        tool_call_map=tool_call_map,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        thinking_text="\n".join(thinking_parts),
    )


# ---------------------------------------------------------------------------
# Ollama parser
# ---------------------------------------------------------------------------

def parse_ollama(req_body: dict, resp_body: dict) -> ParsedRequest:
    model = req_body.get("model")
    raw_messages = req_body.get("messages", [])

    messages: list[ParsedMessage] = []
    for msg in raw_messages:
        role = msg.get("role", "user")
        content = _content_to_str(msg.get("content", ""))
        messages.append(ParsedMessage(role=role, content=content))

    # Provider usage (Ollama uses prompt_eval_count / eval_count)
    provider_input = resp_body.get("prompt_eval_count")
    provider_output = resp_body.get("eval_count")
    response_msg = resp_body.get("message", {})
    response_text = _content_to_str(response_msg.get("content", ""))

    return ParsedRequest(
        model=model,
        messages=messages,
        tool_definitions_text="",
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        response_text=response_text,
    )


# ---------------------------------------------------------------------------
# OpenAI Responses API parser  (/v1/responses)
# ---------------------------------------------------------------------------

def parse_openai_responses(req_body: dict, resp_body: dict) -> ParsedRequest:
    """Parse the OpenAI Responses API wire format.

    Key differences from Chat Completions:
      - input  (not messages), instructions (not system in messages)
      - function_call / function_call_output items alongside role-based messages
      - output  (not choices), output_text content parts
      - usage.input_tokens / output_tokens  (not prompt_tokens / completion_tokens)
    """
    model = req_body.get("model")
    tools = req_body.get("tools") or []
    tool_defs_text = json.dumps(tools) if tools else ""

    messages: list[ParsedMessage] = []
    tool_call_map: dict[str, str] = {}

    # Top-level system prompt
    instructions = req_body.get("instructions", "")
    if instructions:
        messages.append(ParsedMessage(role="system", content=instructions))

    for item in req_body.get("input", []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        role = item.get("role", "")

        if item_type == "function_call_output":
            call_id = item.get("call_id")
            output = _content_to_str(item.get("output", ""))
            messages.append(ParsedMessage(
                role="tool",
                content=output,
                tool_call_id=call_id,
                is_tool_result=True,
            ))
        elif item_type == "function_call":
            call_id = item.get("call_id") or item.get("id")
            name = item.get("name", "")
            args = item.get("arguments", "")
            if call_id and name:
                tool_call_map[call_id] = name
            content = json.dumps({"name": name, "arguments": args}) if name else args
            messages.append(ParsedMessage(role="assistant", content=content))
        elif role in ("user", "assistant", "system"):
            content_raw = item.get("content", "")
            messages.append(ParsedMessage(
                role=role,
                content=_content_to_str(content_raw),
            ))

    # Usage
    usage = resp_body.get("usage", {})
    provider_input = usage.get("input_tokens")
    provider_output = usage.get("output_tokens")

    # Response text from output items
    response_parts: list[str] = []
    for item in resp_body.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []):
                if isinstance(part, dict):
                    if part.get("type") == "output_text":
                        response_parts.append(part.get("text", ""))
                    elif part.get("type") == "refusal":
                        response_parts.append(part.get("refusal", ""))
        elif item.get("type") == "function_call":
            call_id = item.get("call_id") or item.get("id")
            name = item.get("name", "")
            args = item.get("arguments", "")
            if call_id and name:
                tool_call_map[call_id] = name
            response_parts.append(json.dumps({"name": name, "arguments": args}))

    return ParsedRequest(
        model=model,
        messages=messages,
        tool_definitions_text=tool_defs_text,
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        response_text="\n".join(response_parts),
        tool_call_map=tool_call_map,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _sse_to_anthropic_resp(raw: bytes) -> dict:
    """Convert Anthropic SSE stream bytes into a resp_body dict for parse_anthropic."""
    text = raw.decode("utf-8", errors="replace")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read: int | None = None
    cache_creation: int | None = None
    model: str | None = None
    text_parts: list[str] = []
    thinking_parts: list[str] = []

    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type", "")
        if event_type == "message_start":
            msg = event.get("message", {})
            model = model or msg.get("model")
            usage = msg.get("usage", {})
            if "input_tokens" in usage:
                input_tokens = usage["input_tokens"]
            if "cache_read_input_tokens" in usage:
                cache_read = usage["cache_read_input_tokens"]
            if "cache_creation_input_tokens" in usage:
                cache_creation = usage["cache_creation_input_tokens"]
        elif event_type == "content_block_delta":
            delta = event.get("delta", {})
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                text_parts.append(delta.get("text", ""))
            elif dtype == "thinking_delta":
                thinking_parts.append(delta.get("thinking", ""))
            elif dtype == "input_json_delta":
                # Tool-use argument fragment — include for output token counting
                text_parts.append(delta.get("partial_json", ""))
        elif event_type == "message_delta":
            usage = event.get("usage", {})
            if "output_tokens" in usage:
                output_tokens = usage["output_tokens"]
            # Some providers (e.g. Copilot via Bedrock) report all token counts in
            # message_delta rather than message_start — capture as fallback.
            if "input_tokens" in usage and input_tokens is None:
                input_tokens = usage["input_tokens"]
            if "cache_read_input_tokens" in usage and cache_read is None:
                cache_read = usage["cache_read_input_tokens"]
            if "cache_creation_input_tokens" in usage and cache_creation is None:
                cache_creation = usage["cache_creation_input_tokens"]

    content: list[dict] = []
    if thinking_parts:
        content.append({"type": "thinking", "thinking": "".join(thinking_parts)})
    content.append({"type": "text", "text": "".join(text_parts)})

    resp: dict = {"model": model, "content": content}
    if input_tokens is not None or output_tokens is not None or cache_read is not None or cache_creation is not None:
        resp["usage"] = {
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        }
    return resp


def _sse_to_openai_resp(raw: bytes) -> dict:
    """Convert OpenAI-compatible SSE stream bytes into a normalised resp_body dict."""
    text = raw.decode("utf-8", errors="replace")
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    content_parts: list[str] = []
    tool_arg_parts: dict[int, list[str]] = {}   # index → argument fragments
    finish_reason: str | None = None
    model: str | None = None

    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if event.get("model"):
            model = event["model"]

        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            # Text content
            if delta.get("content"):
                content_parts.append(delta["content"])
            # Tool-call argument fragments (needed for output token counting)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                args = (tc.get("function") or {}).get("arguments")
                if args:
                    tool_arg_parts.setdefault(idx, []).append(args)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        # Usage only present in final chunk on many servers
        usage = event.get("usage") or {}
        if usage.get("prompt_tokens") is not None:
            prompt_tokens = usage["prompt_tokens"]
        if usage.get("completion_tokens") is not None:
            completion_tokens = usage["completion_tokens"]
        details = usage.get("completion_tokens_details") or {}
        if details.get("reasoning_tokens") is not None:
            reasoning_tokens = details["reasoning_tokens"]

    response_content: str | None = "".join(content_parts) if content_parts else None
    tool_calls = [
        {"index": i, "function": {"arguments": "".join(parts)}}
        for i, parts in sorted(tool_arg_parts.items())
    ]

    resp: dict = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": response_content,
                **(({"tool_calls": tool_calls}) if tool_calls else {}),
            },
            "finish_reason": finish_reason,
        }],
    }
    if model:
        resp["model"] = model
    # Only include usage when the server actually reported it
    if prompt_tokens is not None or completion_tokens is not None:
        usage_dict: dict = {
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
        }
        if reasoning_tokens is not None:
            usage_dict["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
        resp["usage"] = usage_dict
    return resp


def _sse_to_openai_responses_resp(raw: bytes) -> dict:
    """Convert OpenAI Responses API SSE stream to a dict for parse_openai_responses.

    Uses named events — type field is in the data payload, not the event: line.
    Usage comes from the response.completed event.
    """
    text = raw.decode("utf-8", errors="replace")
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    # output_index → accumulated text fragments
    text_by_index: dict[int, list[str]] = {}
    # output_index → {name, call_id, args: list[str]}
    fc_by_index: dict[int, dict] = {}

    for line in text.splitlines():
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

        elif etype == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                idx = event.get("output_index", 0)
                fc_by_index.setdefault(idx, {"name": "", "call_id": "", "args": []})
                fc_by_index[idx]["name"] = item.get("name", "")
                fc_by_index[idx]["call_id"] = item.get("call_id") or item.get("id", "")

        elif etype == "response.completed":
            resp_obj = event.get("response", {})
            model = model or resp_obj.get("model")
            usage = resp_obj.get("usage", {})
            if input_tokens is None:
                input_tokens = usage.get("input_tokens")
            if output_tokens is None:
                output_tokens = usage.get("output_tokens")

    output: list[dict] = []
    for idx in sorted(text_by_index):
        output.append({
            "type": "message",
            "content": [{"type": "output_text", "text": "".join(text_by_index[idx])}],
        })
    for idx in sorted(fc_by_index):
        fc = fc_by_index[idx]
        output.append({
            "type": "function_call",
            "call_id": fc["call_id"],
            "name": fc["name"],
            "arguments": "".join(fc["args"]),
        })

    resp: dict = {"model": model, "output": output}
    if input_tokens is not None or output_tokens is not None:
        resp["usage"] = {
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
        }
    return resp


def _wire_format(endpoint: str) -> str | None:
    """Detect the API wire format from the request path.

    Returns one of: "anthropic", "openai", "ollama_native", or None.
    Dispatch is endpoint-based so e.g. Copilot relaying to Claude is parsed
    with the Anthropic parser regardless of which host was detected.
    """
    if "/messages" in endpoint:
        return "anthropic"
    if "/chat/completions" in endpoint or "/completions" in endpoint:
        return "openai"
    if "/responses" in endpoint:
        return "openai_responses"
    if "/api/chat" in endpoint or "/api/generate" in endpoint:
        return "ollama_native"
    return None


def parse_sse_request(
    provider: str, endpoint: str, req_body: dict, raw_sse: bytes
) -> ParsedRequest | None:
    """Parse an SSE streaming response into a ParsedRequest.

    ``provider`` is kept for callers but dispatch is endpoint-based via
    ``_wire_format`` so any host can use any protocol (e.g. Copilot → Claude).
    """
    fmt = _wire_format(endpoint)
    try:
        if fmt == "anthropic":
            resp_body = _sse_to_anthropic_resp(raw_sse)
            return parse_anthropic(req_body, resp_body)
        elif fmt == "openai":
            resp_body = _sse_to_openai_resp(raw_sse)
            return parse_openai(req_body, resp_body)
        elif fmt == "openai_responses":
            resp_body = _sse_to_openai_responses_resp(raw_sse)
            return parse_openai_responses(req_body, resp_body)
        # ollama_native streams NDJSON (not SSE) — handled by the non-SSE path
        return None
    except Exception:
        return None


def parse_request(
    provider: str, endpoint: str, req_body: dict, resp_body: dict
) -> ParsedRequest | None:
    """Parse a non-streaming (buffered) response into a ParsedRequest.

    ``provider`` is kept for callers but dispatch is endpoint-based via
    ``_wire_format`` so any host can use any protocol.
    """
    fmt = _wire_format(endpoint)
    try:
        if fmt == "anthropic":
            return parse_anthropic(req_body, resp_body)
        elif fmt == "openai":
            return parse_openai(req_body, resp_body)
        elif fmt == "openai_responses":
            return parse_openai_responses(req_body, resp_body)
        elif fmt == "ollama_native":
            return parse_ollama(req_body, resp_body)
        return None
    except Exception:
        return None
