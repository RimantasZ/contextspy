"""Shared, provider-neutral evidence types for direct lineage and display groups."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from contextspy.analysis.blocks import Direction
from contextspy.analysis.context_diff import ContextBlock, ContextDelta


@dataclass(frozen=True)
class RequestSnapshot:
    id: str
    session_id: str | None
    session_seq: int | None
    timestamp: datetime
    started_at: datetime | None
    duration_ms: int | None
    provider: str
    model: str | None
    agent: str | None
    endpoint: str
    provider_response_id: str | None
    predecessor_response_id: str | None
    context_fidelity: str
    tokens_total_input: int
    tokens_total_output: int
    stream_hint_source: str | None = None
    stream_hint_digest: str | None = None
    blocks: tuple[ContextBlock, ...] = ()
    external: bool = False

    @property
    def effective_started_at(self) -> datetime:
        if self.started_at is not None:
            return self.started_at
        if self.duration_ms is not None:
            return self.timestamp - timedelta(milliseconds=self.duration_ms)
        return self.timestamp

    @property
    def started_at_source(self) -> str:
        if self.started_at is not None:
            return "observed"
        if self.duration_ms is not None:
            return "estimated"
        return "completion_fallback"


@dataclass
class LineageEdge:
    source_request_id: str
    target_request_id: str
    relation_type: str = "context_continuation"
    certainty: str = "exact"
    confidence: float | None = None
    evidence_source: str = "provider"
    evidence: dict[str, Any] = field(default_factory=dict)
    delta: ContextDelta | None = None
    external_source: bool = False

    def to_dict(self, *, include_mappings: bool = False) -> dict[str, Any]:
        return {
            "source_request_id": self.source_request_id,
            "target_request_id": self.target_request_id,
            "relation_type": self.relation_type,
            "certainty": self.certainty,
            "confidence": self.confidence,
            "evidence_source": self.evidence_source,
            "evidence": self.evidence,
            "delta": (
                self.delta.to_dict(include_mappings=include_mappings)
                if self.delta is not None else None
            ),
            "external_source": self.external_source,
        }


def request_order(request: RequestSnapshot) -> tuple[datetime, datetime, int, str]:
    return (
        request.effective_started_at,
        request.timestamp,
        request.session_seq if request.session_seq is not None else 2**31,
        request.id,
    )


def input_blocks(request: RequestSnapshot) -> list[ContextBlock]:
    return [block for block in request.blocks if block.direction == Direction.INPUT]


def output_blocks(request: RequestSnapshot) -> list[ContextBlock]:
    return [block for block in request.blocks if block.direction == Direction.OUTPUT]
