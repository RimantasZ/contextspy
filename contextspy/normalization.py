# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Provider-state normalization between transport capture and JSON analysis."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from contextspy.analysis.capture import CapturedEvent
from contextspy.analysis.invocations import CanonicalInvocation, CanonicalJsonDocument


@dataclass(frozen=True)
class ObservedInvocation:
    """Decoded application data for one externally observable invocation."""

    provider: str
    provider_protocol: str
    protocol_id: str
    request_payload: dict[str, Any]
    observed_request_text: str | None
    response: CanonicalJsonDocument | None
    events: tuple[CapturedEvent, ...] = ()
    outcome: str = "unknown"


@dataclass(frozen=True)
class PersistedCanonicalInvocation:
    request: CanonicalJsonDocument
    response: CanonicalJsonDocument | None
    context_fidelity: str = "complete"
    outcome: str = "completed"
    provider_protocol: str | None = None


class InvocationLineageRepository(Protocol):
    def get(
        self, provider: str, response_id: str,
    ) -> PersistedCanonicalInvocation | None: ...


class ProviderInvocationNormalizer(Protocol):
    provider_protocol: str

    def normalize(
        self,
        observed: ObservedInvocation,
        lineage: InvocationLineageRepository,
    ) -> CanonicalInvocation: ...


def _observed_request_document(observed: ObservedInvocation) -> CanonicalJsonDocument:
    """Retain exact REST JSON when it represents the observed value unchanged."""
    if observed.observed_request_text is not None:
        try:
            document = CanonicalJsonDocument.from_text(observed.observed_request_text)
        except (ValueError, TypeError):
            pass
        else:
            if document.value == observed.request_payload:
                return document
    return CanonicalJsonDocument.from_value(observed.request_payload)


def _response_id(response: CanonicalJsonDocument | None) -> str | None:
    if response is None:
        return None
    value = response.value.get("id")
    return value if isinstance(value, str) and value else None


def _anthropic_hidden_content(value: Any) -> bool:
    """Detect content whose original text cannot be recovered from capture."""
    if isinstance(value, dict):
        kind = value.get("type")
        if kind in {"redacted_thinking", "compaction"}:
            return True
        if kind == "thinking" and not value.get("thinking"):
            return True
        return any(_anthropic_hidden_content(child) for child in value.values())
    if isinstance(value, list):
        return any(_anthropic_hidden_content(child) for child in value)
    return False


class IdentityInvocationNormalizer:
    provider_protocol = "*"

    def normalize(
        self,
        observed: ObservedInvocation,
        lineage: InvocationLineageRepository,
    ) -> CanonicalInvocation:
        del lineage
        return CanonicalInvocation(
            request=_observed_request_document(observed),
            response=observed.response,
            provider_response_id=_response_id(observed.response),
            outcome=observed.outcome,
        )


def _input_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return deepcopy(value)
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    return [deepcopy(value)]


def _contains_opaque_state(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"compaction", "compaction_trigger"}:
            return True
        if value.get("encrypted_content"):
            return True
        return any(_contains_opaque_state(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_opaque_state(child) for child in value)
    return False


def _after_last_compaction(items: list[Any]) -> list[Any]:
    """Drop visible history superseded by the provider's latest compaction."""
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, dict) and item.get("type") in {
            "compaction", "compaction_trigger",
        }:
            return items[index:]
    return items


def _injected_input(events: tuple[CapturedEvent, ...]) -> list[Any]:
    items: list[Any] = []
    for captured in events:
        if captured.direction != "client_to_server" or not isinstance(captured.payload, dict):
            continue
        event = captured.payload
        if event.get("type") != "response.inject":
            continue
        if "input" in event:
            items.extend(_input_items(event.get("input")))
        elif "items" in event:
            items.extend(_input_items(event.get("items")))
        elif "item" in event:
            items.extend(_input_items(event.get("item")))
    return items


def _normalize_tool_value(value: Any) -> Any:
    """Normalize response-echoed tool metadata for semantic comparison."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            # Response snapshots add optional null fields such as
            # ``output_schema`` that are absent from the request item.
            if key == "output_schema" and child is None:
                continue
            if key == "tools" and isinstance(child, list):
                normalized[key] = _normalize_tool_collection(child)
            else:
                normalized[key] = _normalize_tool_value(child)
        return normalized
    if isinstance(value, list):
        return [_normalize_tool_value(child) for child in value]
    return value


def _normalize_tool_collection(tools: list[Any]) -> list[Any]:
    """Treat tool and namespace-member order as non-semantic."""
    normalized = [_normalize_tool_value(tool) for tool in tools]
    return sorted(
        normalized,
        key=lambda tool: json.dumps(
            tool, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
    )


def _response_tools_already_in_input(
    tools: Any, input_items: list[Any],
) -> bool:
    """Return whether a response tool echo duplicates an input control item."""
    if not isinstance(tools, list):
        return False
    normalized_tools = _normalize_tool_collection(tools)
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        embedded_tools = item.get("tools")
        if (
            isinstance(embedded_tools, list)
            and _normalize_tool_collection(embedded_tools) == normalized_tools
        ):
            return True
    return False


class OpenAIResponsesInvocationNormalizer:
    """Expand explicit Responses lineage into a standalone provider request."""

    provider_protocol = "openai_responses"

    def normalize(
        self,
        observed: ObservedInvocation,
        lineage: InvocationLineageRepository,
    ) -> CanonicalInvocation:
        request = deepcopy(observed.request_payload)
        had_ws_envelope = request.get("type") == "response.create"
        if had_ws_envelope:
            request.pop("type", None)

        # A response snapshot may echo the configuration actually applied to
        # this invocation. Fill only from the current response, never from the
        # predecessor's top-level options.
        tools_filled_from_response = False
        if had_ws_envelope and observed.response is not None:
            response_value = observed.response.value
            for key in (
                "model", "instructions", "tools", "tool_choice", "reasoning",
                "text", "parallel_tool_calls", "max_output_tokens",
            ):
                if key not in request and response_value.get(key) is not None:
                    request[key] = deepcopy(response_value[key])
                    if key == "tools":
                        tools_filled_from_response = True

        predecessor = request.pop("previous_response_id", None)
        if not isinstance(predecessor, str) or not predecessor:
            predecessor = None

        current_input = _input_items(request.get("input"))
        injected = _injected_input(observed.events)
        if injected:
            current_input.extend(injected)

        fidelity = "complete"
        notes: list[str] = []
        if request.get("conversation"):
            fidelity = "partial"
            notes.append(
                "Provider-managed conversation history is not available in this capture"
            )
        if predecessor is not None:
            previous = lineage.get(observed.provider, predecessor)
            if previous is None:
                fidelity = "partial"
                notes.append("A referenced earlier response was not captured or retained")
            else:
                previous_input = _input_items(previous.request.value.get("input"))
                previous_output: list[Any] = []
                if previous.response is not None:
                    previous_output = _input_items(previous.response.value.get("output"))
                else:
                    fidelity = "partial"
                    notes.append("A referenced earlier response has no retained response body")
                current_input = _after_last_compaction(
                    previous_input + previous_output
                ) + current_input
                if previous.context_fidelity == "partial":
                    fidelity = "partial"
                    notes.append("An earlier predecessor in this chain is unavailable")
                elif previous.context_fidelity == "opaque" and fidelity == "complete":
                    fidelity = "opaque"
                    notes.append("An earlier item in this chain contains opaque provider state")

        # Codex WebSocket requests can carry tools in an ``additional_tools``
        # input item while response snapshots echo the same effective set as
        # top-level ``tools``. The latter is reconstruction metadata, not a
        # second copy of the request context.
        if (
            tools_filled_from_response
            and _response_tools_already_in_input(request.get("tools"), current_input)
        ):
            request.pop("tools", None)

        request["input"] = current_input
        if _contains_opaque_state(current_input):
            if fidelity == "complete":
                fidelity = "opaque"
            notes.append("The provider supplied compacted or encrypted context")

        # Exact observed text is retained only when normalization made no
        # semantic change. Stateful/WS requests are serialized once here.
        if predecessor is None and not had_ws_envelope and not injected:
            canonical_request = _observed_request_document(observed)
        else:
            canonical_request = CanonicalJsonDocument.from_value(request)

        return CanonicalInvocation(
            request=canonical_request,
            response=observed.response,
            provider_response_id=_response_id(observed.response),
            predecessor_response_id=predecessor,
            outcome=observed.outcome,
            context_fidelity=fidelity,
            context_notes=tuple(dict.fromkeys(notes)),
        )


class AnthropicThreadInvocationNormalizer:
    """Expand the observable history carried by Anthropic thread continuations.

    The wire request is retained separately.  In particular, cache diagnostics
    are not lineage evidence: only ``thread.previous_message_id`` is followed.
    """

    provider_protocol = "anthropic"

    def normalize(
        self,
        observed: ObservedInvocation,
        lineage: InvocationLineageRepository,
    ) -> CanonicalInvocation:
        thread = observed.request_payload.get("thread")
        if not isinstance(thread, dict):
            return _IDENTITY.normalize(observed, lineage)

        mode = thread.get("type")
        predecessor = thread.get("previous_message_id")
        predecessor = predecessor if isinstance(predecessor, str) and predecessor else None
        notes: list[str] = []
        fidelity = "complete"
        request = deepcopy(observed.request_payload)
        request.pop("thread", None)

        if mode == "continue":
            if predecessor is None:
                fidelity = "partial"
                notes.append("Thread continuation has no valid predecessor message ID")
            elif predecessor == _response_id(observed.response):
                fidelity = "partial"
                notes.append("Thread continuation refers to its own response ID")
            else:
                previous = lineage.get(observed.provider, predecessor)
                if previous is None:
                    fidelity = "partial"
                    notes.append("A referenced earlier thread response was not captured or retained")
                elif previous.provider_protocol not in {None, "anthropic"}:
                    fidelity = "partial"
                    notes.append("Referenced thread response uses a different provider protocol")
                elif (
                    previous.response is None
                    or previous.outcome in {"failed", "incomplete"}
                    or _response_id(previous.response) != predecessor
                ):
                    fidelity = "partial"
                    notes.append("A referenced earlier thread response is missing, incomplete, or inconsistent")
                else:
                    previous_messages = previous.request.value.get("messages")
                    current_messages = request.get("messages")
                    response = previous.response.value
                    output = response.get("content")
                    if (
                        not isinstance(previous_messages, list)
                        or not isinstance(current_messages, list)
                        or not isinstance(output, list)
                        or response.get("role") != "assistant"
                        or response.get("error")
                    ):
                        fidelity = "partial"
                        notes.append("Thread history or predecessor output has an unsupported shape")
                    else:
                        # A previous assistant turn is part of the next input,
                        # not the whole response envelope (usage/metadata).
                        assistant = {"role": "assistant", "content": deepcopy(output)}
                        prefix = deepcopy(previous_messages) + [assistant]
                        delta = deepcopy(current_messages)
                        if delta[:len(prefix)] == prefix:
                            # Some clients may send an expanded prefix while
                            # still referencing thread state.
                            request["messages"] = delta
                        elif delta[:1] == [assistant]:
                            request["messages"] = deepcopy(previous_messages) + delta
                        else:
                            request["messages"] = prefix + delta
                        prior_request = previous.request.value
                        if "tools" not in request and "tools" in prior_request:
                            request["tools"] = deepcopy(prior_request["tools"])
                        if "system" not in request and "system" in prior_request:
                            request["system"] = deepcopy(prior_request["system"])
                        elif (
                            isinstance(request.get("system"), list)
                            and isinstance(prior_request.get("system"), list)
                            and 0 < len(request["system"]) < len(prior_request["system"])
                        ):
                            # Observed thread traffic sends a changed leading
                            # system block while omitting the stable tail.
                            # The inheritance rule is not publicly specified.
                            request["system"] += deepcopy(
                                prior_request["system"][len(request["system"]):]
                            )
                            fidelity = "partial"
                            notes.append("Inherited system-block tail is inferred from thread history")
                        if previous.context_fidelity == "partial":
                            fidelity = "partial"
                            notes.append("An earlier predecessor in this thread is unavailable")
                        elif previous.context_fidelity == "opaque" and fidelity == "complete":
                            fidelity = "opaque"
                            notes.append("An earlier predecessor contains opaque provider state")
        elif mode != "create":
            fidelity = "partial"
            notes.append("Unrecognized Anthropic thread operation")

        # Context edits happen at the provider, after the logical request is
        # assembled.  The returned edit summary gives counts, not full text.
        management = observed.request_payload.get("context_management")
        edits = management.get("edits") if isinstance(management, dict) else None
        response_management = (
            observed.response.value.get("context_management")
            if observed.response is not None else None
        )
        applied = (
            response_management.get("applied_edits")
            if isinstance(response_management, dict) else None
        )
        if applied or (edits and applied is None):
            if fidelity == "complete":
                fidelity = "opaque"
            notes.append("Provider-side context editing may hide the exact model input")
        if _anthropic_hidden_content(request.get("messages")):
            if fidelity == "complete":
                fidelity = "opaque"
            notes.append("Thread history contains hidden or redacted content")

        return CanonicalInvocation(
            request=(
                _observed_request_document(observed) if mode == "create"
                else CanonicalJsonDocument.from_value(request)
            ),
            response=observed.response,
            provider_response_id=_response_id(observed.response),
            predecessor_response_id=predecessor if mode == "continue" else None,
            outcome=observed.outcome,
            context_fidelity=fidelity,
            context_notes=tuple(dict.fromkeys(notes)),
        )


_IDENTITY = IdentityInvocationNormalizer()
_NORMALIZERS: dict[str, ProviderInvocationNormalizer] = {
    OpenAIResponsesInvocationNormalizer.provider_protocol:
        OpenAIResponsesInvocationNormalizer(),
    AnthropicThreadInvocationNormalizer.provider_protocol:
        AnthropicThreadInvocationNormalizer(),
}


def register_normalizer(normalizer: ProviderInvocationNormalizer) -> None:
    _NORMALIZERS[normalizer.provider_protocol] = normalizer


def normalize_invocation(
    observed: ObservedInvocation,
    lineage: InvocationLineageRepository,
) -> CanonicalInvocation:
    normalizer = _NORMALIZERS.get(observed.provider_protocol, _IDENTITY)
    return normalizer.normalize(observed, lineage)
