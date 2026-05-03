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

        # Build call_id → tool_name map from assistant tool_calls
        for tc in msg.get("tool_calls") or []:
            call_id = tc.get("id")
            name = (tc.get("function") or {}).get("name") or tc.get("name")
            if call_id and name:
                tool_call_map[call_id] = name

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
    response_text = ""
    usage = resp_body.get("usage", {})
    if usage:
        provider_input = usage.get("prompt_tokens") or usage.get("input_tokens")
        provider_output = usage.get("completion_tokens") or usage.get("output_tokens")
    choices = resp_body.get("choices", [])
    if choices:
        msg = choices[0].get("message") or choices[0].get("delta") or {}
        response_text = _content_to_str(msg.get("content", ""))

    return ParsedRequest(
        model=model,
        messages=messages,
        tool_definitions_text=tool_defs_text,
        provider_input_tokens=provider_input,
        provider_output_tokens=provider_output,
        response_text=response_text,
        tool_call_map=tool_call_map,
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
    response_text = ""
    usage = resp_body.get("usage", {})
    if usage:
        provider_input = usage.get("input_tokens")
        provider_output = usage.get("output_tokens")
    resp_content = resp_body.get("content", [])
    if isinstance(resp_content, list):
        response_text = _content_to_str(resp_content)
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
# Dispatch
# ---------------------------------------------------------------------------

def _sse_to_anthropic_resp(raw: bytes) -> dict:
    """Convert Anthropic SSE stream bytes into a resp_body dict for parse_anthropic."""
    text = raw.decode("utf-8", errors="replace")
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    text_parts: list[str] = []

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
            input_tokens = usage.get("input_tokens", input_tokens)
        elif event_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
        elif event_type == "message_delta":
            usage = event.get("usage", {})
            output_tokens = usage.get("output_tokens", output_tokens)

    return {
        "model": model,
        "content": [{"type": "text", "text": "".join(text_parts)}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _sse_to_openai_resp(raw: bytes) -> dict:
    """Convert OpenAI SSE stream bytes into a resp_body dict for parse_openai."""
    text = raw.decode("utf-8", errors="replace")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    text_parts: list[str] = []

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
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            text_parts.append(delta.get("content") or "")
        usage = event.get("usage") or {}
        if usage.get("prompt_tokens"):
            prompt_tokens = usage["prompt_tokens"]
        if usage.get("completion_tokens"):
            completion_tokens = usage["completion_tokens"]

    return {
        "choices": [{"message": {"role": "assistant", "content": "".join(text_parts)}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def parse_sse_request(
    provider: str, endpoint: str, req_body: dict, raw_sse: bytes
) -> ParsedRequest | None:
    """Parse an SSE streaming response into a ParsedRequest."""
    try:
        if provider in ("openai", "openai_azure", "copilot"):
            if "/chat/completions" in endpoint:
                resp_body = _sse_to_openai_resp(raw_sse)
                return parse_openai(req_body, resp_body)
        elif provider == "anthropic":
            if "/messages" in endpoint:
                resp_body = _sse_to_anthropic_resp(raw_sse)
                return parse_anthropic(req_body, resp_body)
        return None
    except Exception:
        return None


def parse_request(
    provider: str, endpoint: str, req_body: dict, resp_body: dict
) -> ParsedRequest | None:
    try:
        if provider in ("openai", "openai_azure", "copilot"):
            if "/chat/completions" in endpoint:
                return parse_openai(req_body, resp_body)
        elif provider == "anthropic":
            if "/messages" in endpoint:
                return parse_anthropic(req_body, resp_body)
        elif provider == "ollama":
            if "/api/chat" in endpoint:
                return parse_ollama(req_body, resp_body)
        return None
    except Exception:
        return None
