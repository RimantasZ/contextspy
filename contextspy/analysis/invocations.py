# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Canonical invocation documents and the transport-neutral analysis entry point."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from contextspy.analysis.adapters.base import WireFormatAdapter
from contextspy.analysis.blocks import AnalyzedRequest, Usage


@dataclass(frozen=True)
class CanonicalJsonDocument:
    """One exact JSON serialization coupled to the value analyzed from it.

    ``text`` is the value persisted and displayed. ``value`` is always parsed
    from that exact text, which prevents persistence and block analysis from
    silently observing different documents.
    """

    text: str
    value: dict[str, Any]

    @classmethod
    def from_text(cls, text: str) -> "CanonicalJsonDocument":
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("Canonical JSON document must be an object")
        return cls(text=text, value=value)

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "CanonicalJsonDocument":
        if not isinstance(value, dict):
            raise ValueError("Canonical JSON document must be an object")
        text = json.dumps(value, ensure_ascii=False)
        # Parse the stored serialization so mutable caller-owned values cannot
        # diverge from the exact document that will be retained in the DB.
        parsed = json.loads(text)
        return cls(text=text, value=parsed)


@dataclass(frozen=True)
class CanonicalInvocation:
    """A standalone provider request/response pair, independent of transport."""

    request: CanonicalJsonDocument
    response: CanonicalJsonDocument | None
    provider_response_id: str | None = None
    predecessor_response_id: str | None = None
    outcome: str = "unknown"
    context_fidelity: str = "complete"
    context_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisIssue:
    stage: str
    error: Exception


@dataclass(frozen=True)
class CanonicalAnalysis:
    analyzed: AnalyzedRequest
    issues: tuple[AnalysisIssue, ...] = ()


def analyze_invocation(
    canonical: CanonicalInvocation,
    adapter: WireFormatAdapter,
) -> CanonicalAnalysis:
    """Parse only canonical provider JSON, keeping request/response failures isolated."""

    issues: list[AnalysisIssue] = []
    try:
        input_blocks, tool_call_map = adapter.parse_request(canonical.request.value)
    except Exception as exc:  # capture must survive an adapter regression
        input_blocks, tool_call_map = [], {}
        issues.append(AnalysisIssue("request_analysis", exc))

    output_blocks = []
    usage = Usage()
    if canonical.response is not None:
        try:
            output_blocks, usage = adapter.parse_response(canonical.response.value)
        except Exception as exc:  # response analysis is independent of request analysis
            issues.append(AnalysisIssue("response_analysis", exc))

    return CanonicalAnalysis(
        analyzed=AnalyzedRequest(
            model=canonical.request.value.get("model"),
            input_blocks=input_blocks,
            output_blocks=output_blocks,
            usage=usage,
            tool_call_map=tool_call_map,
        ),
        issues=tuple(issues),
    )

