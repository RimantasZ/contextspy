# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Provider-extensible conversation identity and continuation semantics.

Wire-format adapters answer "what did this payload contain?".  Conversation
adapters answer the separate questions "which model invocation is this?",
"which logical user turn owns it?", and "how should visible predecessor state
be carried into the next invocation?".

The context reconstruction engine consumes the normalized dataclasses below;
it contains no Codex- or OpenAI-specific field lookups.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from contextspy.analysis.blocks import BlockType


@dataclass(frozen=True)
class InvocationIdentity:
    provider_request_id: str | None = None
    previous_provider_request_id: str | None = None
    provider_conversation_id: str | None = None
    logical_turn_id: str | None = None
    agent_id: str | None = None
    parent_turn_id: str | None = None
    confidence: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogicalRequestKey:
    provider: str
    conversation_id: str
    turn_id: str
    agent_id: str | None = None

    def serialize(self) -> str:
        # JSON avoids delimiter escaping and makes the stored key inspectable.
        return json.dumps(
            [self.provider, self.conversation_id, self.turn_id, self.agent_id],
            separators=(",", ":"), ensure_ascii=False,
        )


@dataclass(frozen=True)
class ContextMutation:
    """Normalized instructions for constructing an invocation's visible context."""

    inherit_previous: bool = False
    include_previous_output: bool = False
    drop_inherited_block_types: tuple[str, ...] = ()
    completeness: str = "observed"
    operations: tuple[str, ...] = ("reset", "append_current_input")
    warnings: tuple[str, ...] = ()


class ConversationAdapter(ABC):
    adapter_id: str

    @abstractmethod
    def matches(
        self, *, provider: str, endpoint: str, transport: str, request_body: dict,
    ) -> bool:
        """Whether this adapter understands the request's continuation semantics."""

    @abstractmethod
    def identify(
        self, *, provider: str, agent: str, request_body: dict,
        response_body: dict | None,
    ) -> InvocationIdentity:
        """Extract provider IDs and logical-turn metadata."""

    @abstractmethod
    def logical_request_key(
        self, *, provider: str, identity: InvocationIdentity,
    ) -> LogicalRequestKey | None:
        """Return an authoritative logical grouping key, when available."""

    @abstractmethod
    def context_mutation(
        self, *, request_body: dict, identity: InvocationIdentity,
    ) -> ContextMutation:
        """Describe how captured predecessor state contributes to this invocation."""


def _conversation_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        candidate = value.get("id")
        return candidate if isinstance(candidate, str) and candidate else None
    return None


class OpenAIResponsesConversationAdapter(ConversationAdapter):
    """Conversation semantics shared by OpenAI-compatible Responses endpoints."""

    adapter_id = "openai_responses_conversation"

    def matches(
        self, *, provider: str, endpoint: str, transport: str, request_body: dict,
    ) -> bool:
        return "/responses" in endpoint

    def identify(
        self, *, provider: str, agent: str, request_body: dict,
        response_body: dict | None,
    ) -> InvocationIdentity:
        response_body = response_body or {}
        provider_request_id = response_body.get("id")
        if not isinstance(provider_request_id, str):
            provider_request_id = None
        previous_id = request_body.get("previous_response_id")
        if not isinstance(previous_id, str):
            previous_id = None
        conversation_id = (
            _conversation_id(request_body.get("conversation"))
            or _conversation_id(response_body.get("conversation"))
        )
        confidence = "explicit" if provider_request_id or previous_id or conversation_id else "unknown"
        return InvocationIdentity(
            provider_request_id=provider_request_id,
            previous_provider_request_id=previous_id,
            provider_conversation_id=conversation_id,
            agent_id=agent,
            confidence=confidence,
        )

    def logical_request_key(
        self, *, provider: str, identity: InvocationIdentity,
    ) -> LogicalRequestKey | None:
        # The public Responses schema has conversation lineage but no portable
        # user-turn identifier.  The core joins a continuation to its
        # predecessor's logical request; roots remain singleton groups.
        return None

    def context_mutation(
        self, *, request_body: dict, identity: InvocationIdentity,
    ) -> ContextMutation:
        has_compaction = any(
            isinstance(item, dict) and item.get("type") == "compaction"
            for item in (request_body.get("input") or [])
        )
        if has_compaction:
            return ContextMutation(
                inherit_previous=False,
                include_previous_output=False,
                completeness="compacted",
                operations=("reset_to_compaction", "append_current_input"),
                warnings=(
                    "The provider supplied opaque compacted state; its original "
                    "content cannot be reconstructed from the capture.",
                ),
            )
        # A provider conversation id extracted from Codex client metadata is a
        # grouping hint, not proof that this particular response inherits
        # state. Only the public continuation fields imply inheritance.
        continued = bool(
            identity.previous_provider_request_id
            or _conversation_id(request_body.get("conversation"))
        )
        if not continued:
            return ContextMutation()
        return ContextMutation(
            inherit_previous=True,
            include_previous_output=True,
            # Responses documentation explicitly says previous instructions
            # are not carried with previous_response_id. Tool declarations are
            # request configuration, not conversation items, so only currently
            # observed definitions are included.
            drop_inherited_block_types=(
                BlockType.SYSTEM_PROMPT,
                BlockType.TOOL_DEFINITION,
            ),
            completeness="best_effort",
            operations=(
                "inherit_previous_input",
                "drop_previous_instructions",
                "drop_previous_tool_definitions",
                "append_previous_output",
                "append_current_input",
            ),
            warnings=(
                "Provider-managed state may contain instructions, reasoning, "
                "compaction, or formatting not observable on the wire.",
            ),
        )


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _codex_metadata(request_body: dict) -> dict[str, Any]:
    """Collect stable Codex turn fields from known metadata envelope variants."""
    sources: list[dict[str, Any]] = [request_body]
    for key in ("client_metadata", "metadata"):
        value = _as_mapping(request_body.get(key))
        if value:
            sources.append(value)
    for source in list(sources):
        for key in (
            "x-codex-turn-metadata", "x_codex_turn_metadata",
            "codex_turn_metadata", "turn_metadata",
        ):
            value = _as_mapping(source.get(key))
            if value:
                sources.append(value)

    result: dict[str, Any] = {}
    aliases = {
        "thread_id": ("thread_id", "threadId"),
        "session_id": ("session_id", "sessionId"),
        "turn_id": ("turn_id", "turnId"),
        "root_turn_id": ("root_turn_id", "rootTurnId"),
        "parent_turn_id": ("parent_turn_id", "parentTurnId"),
        "agent_name": ("agent_name", "agentName"),
        "request_kind": ("request_kind", "requestKind"),
    }
    for canonical, names in aliases.items():
        for source in reversed(sources):
            found = next((source.get(name) for name in names if source.get(name) is not None), None)
            if found is not None:
                result[canonical] = found
                break
    return result


class CodexConversationAdapter(OpenAIResponsesConversationAdapter):
    adapter_id = "codex_responses_conversation"

    def matches(
        self, *, provider: str, endpoint: str, transport: str, request_body: dict,
    ) -> bool:
        return "/backend-api/codex/responses" in endpoint

    def identify(
        self, *, provider: str, agent: str, request_body: dict,
        response_body: dict | None,
    ) -> InvocationIdentity:
        base = super().identify(
            provider=provider, agent=agent, request_body=request_body,
            response_body=response_body,
        )
        metadata = _codex_metadata(request_body)
        conversation_id = metadata.get("thread_id") or metadata.get("session_id")
        turn_id = metadata.get("root_turn_id") or metadata.get("turn_id")
        agent_id = metadata.get("agent_name") or agent
        explicit_group = bool(conversation_id and turn_id)
        return InvocationIdentity(
            provider_request_id=base.provider_request_id,
            previous_provider_request_id=base.previous_provider_request_id,
            provider_conversation_id=str(conversation_id) if conversation_id else base.provider_conversation_id,
            logical_turn_id=str(turn_id) if turn_id else None,
            agent_id=str(agent_id) if agent_id else None,
            parent_turn_id=(
                str(metadata["parent_turn_id"])
                if metadata.get("parent_turn_id") is not None else None
            ),
            confidence="explicit" if explicit_group else base.confidence,
            metadata=metadata,
        )

    def logical_request_key(
        self, *, provider: str, identity: InvocationIdentity,
    ) -> LogicalRequestKey | None:
        if not identity.provider_conversation_id or not identity.logical_turn_id:
            return None
        return LogicalRequestKey(
            provider=provider,
            conversation_id=identity.provider_conversation_id,
            turn_id=identity.logical_turn_id,
            agent_id=identity.agent_id,
        )


REGISTRY: list[ConversationAdapter] = [
    CodexConversationAdapter(),
    OpenAIResponsesConversationAdapter(),
]


def get_conversation_adapter(
    *, provider: str, endpoint: str, transport: str, request_body: dict,
) -> ConversationAdapter | None:
    for adapter in REGISTRY:
        if adapter.matches(
            provider=provider, endpoint=endpoint, transport=transport,
            request_body=request_body,
        ):
            return adapter
    return None


__all__ = [
    "ContextMutation",
    "ConversationAdapter",
    "InvocationIdentity",
    "LogicalRequestKey",
    "get_conversation_adapter",
]
