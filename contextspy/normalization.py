# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Provider-state normalization between transport capture and JSON analysis."""
from __future__ import annotations

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
        if had_ws_envelope and observed.response is not None:
            response_value = observed.response.value
            for key in (
                "model", "instructions", "tools", "tool_choice", "reasoning",
                "text", "parallel_tool_calls", "max_output_tokens",
            ):
                if key not in request and response_value.get(key) is not None:
                    request[key] = deepcopy(response_value[key])

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


_IDENTITY = IdentityInvocationNormalizer()
_NORMALIZERS: dict[str, ProviderInvocationNormalizer] = {
    OpenAIResponsesInvocationNormalizer.provider_protocol:
        OpenAIResponsesInvocationNormalizer(),
}


def register_normalizer(normalizer: ProviderInvocationNormalizer) -> None:
    _NORMALIZERS[normalizer.provider_protocol] = normalizer


def normalize_invocation(
    observed: ObservedInvocation,
    lineage: InvocationLineageRepository,
) -> CanonicalInvocation:
    normalizer = _NORMALIZERS.get(observed.provider_protocol, _IDENTITY)
    return normalizer.normalize(observed, lineage)
