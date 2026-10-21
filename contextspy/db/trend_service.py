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
"""Per-request metric series for the session trend chart.

Every number the chart plots is computed here (AGENTS.md: analysis lives in Python); the UI only
formats and lays out. A metric is one entry in ``METRICS``: adding one needs no client change
beyond unit formatting. A ``composition`` metric is a stacked breakdown (``parts``) instead of one
number: its per-request part values travel in each point's ``parts``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession, load_only

from contextspy.analysis.accounting import cached_share_pct
from contextspy.analysis.blocks import Direction
from contextspy.db.models import BlockRecord, Request

BlockCounts = dict[str, int]


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str  # "tokens" | "percent" | "ms" | "count"
    estimated: bool
    empty_hint: str | None
    compute: Callable[[Request, BlockCounts], float | int | None]
    kind: str = "scalar"  # "scalar" | "composition"
    # composition only: (part id, label, Request column holding its tokens), in stacking order
    parts: tuple[tuple[str, str, str], ...] = ()


_CONTEXT_PARTS = (
    ("system_prompt", "System Prompt", "tokens_system_prompt"),
    ("tool_definitions", "Tool Definitions", "tokens_tool_definitions"),
    ("tool_results", "Tool Results", "tokens_tool_results"),
    ("file_contents", "File Contents", "tokens_file_contents"),
    ("conversation_history", "Conversation History", "tokens_conversation_history"),
    ("current_user_message", "User Message", "tokens_current_user_message"),
    ("assistant_prefill", "Assistant Prefill", "tokens_assistant_prefill"),
    ("uncategorized", "Uncategorized", "tokens_uncategorized"),
)
_OUTPUT_PARTS = (
    ("output_text", "Text", "tokens_output_text"),
    ("output_thinking", "Thinking", "tokens_output_thinking"),
)


def _parts_total(parts: tuple[tuple[str, str, str], ...]) -> Callable[[Request, BlockCounts], int]:
    return lambda r, _counts: sum(getattr(r, column) or 0 for _id, _label, column in parts)


METRICS: tuple[Metric, ...] = (
    Metric("context_estimated", "Context size (estimated)", "tokens", True, None,
           lambda r, _c: r.tokens_total_input),
    Metric("context_composition", "Context composition (estimated)", "tokens", True, None,
           _parts_total(_CONTEXT_PARTS), kind="composition", parts=_CONTEXT_PARTS),
    Metric("cache_hit_pct", "Cache hit %", "percent", False,
           "Provider did not report cache usage",
           lambda r, _c: cached_share_pct(r.cache_read_tokens, r.provider_input_tokens)),
    Metric("output_estimated", "Output tokens (estimated)", "tokens", True, None,
           lambda r, _c: r.tokens_total_output),
    Metric("output_composition", "Output composition (estimated)", "tokens", True, None,
           _parts_total(_OUTPUT_PARTS), kind="composition", parts=_OUTPUT_PARTS),
    Metric("block_count", "Blocks (input)", "count", False, "No block data for these requests",
           lambda r, counts: counts.get(r.id)),
    Metric("ttft_ms", "TTFT", "ms", False,
           "No streamed responses (TTFT needs streaming)", lambda r, _c: r.ttft_ms),
    Metric("duration_ms", "Latency", "ms", False,
           "No completed responses", lambda r, _c: r.duration_ms),
)

_COLUMNS = (
    Request.id, Request.session_id, Request.session_seq, Request.timestamp, Request.started_at,
    Request.purpose, Request.context_fidelity, Request.tokens_total_input, Request.tokens_total_output,
    Request.provider_input_tokens, Request.cache_read_tokens, Request.ttft_ms, Request.duration_ms,
    *(getattr(Request, column) for _id, _label, column in _CONTEXT_PARTS + _OUTPUT_PARTS),
)


def input_block_counts(db: OrmSession, session_id: str) -> BlockCounts:
    """Input blocks per request of the session in one grouped pass over ``blocks``."""
    rows = db.execute(
        select(BlockRecord.request_id, func.count())
        .join(Request, Request.id == BlockRecord.request_id)
        .where(Request.session_id == session_id, BlockRecord.direction == Direction.INPUT)
        .group_by(BlockRecord.request_id)
    ).all()
    return {request_id: count for request_id, count in rows}


def _point(r: Request, ordinal: int, counts: BlockCounts) -> dict[str, Any]:
    when = r.started_at or r.timestamp
    return {
        "request_id": r.id,
        "session_seq": r.session_seq,
        "ordinal": ordinal,  # 1-based position in the session; Request-axis fallback for null seq
        "time": when.isoformat(),
        "context_fidelity": r.context_fidelity,
        "purpose": r.purpose,
        "values": {m.id: m.compute(r, counts) for m in METRICS},
        "parts": {
            m.id: {part_id: getattr(r, column) or 0 for part_id, _label, column in m.parts}
            for m in METRICS if m.kind == "composition"
        },
    }


def _sort_key(r: Request) -> tuple:
    return (r.session_seq is None, r.session_seq or 0, r.started_at or r.timestamp, r.id)


def build_session_trend(db: OrmSession, session_id: str, graph: dict) -> dict[str, Any]:
    """One series per conversation group (plus auxiliary) of ``graph``, one point per request.

    Ids that are not rows of this session (external lineage parents) are dropped; a request listed
    in several groups is plotted only in the first one; a session request in no group falls into
    the auxiliary series.
    """
    rows = list(db.execute(
        select(Request).options(load_only(*_COLUMNS, raiseload=True))
        .where(Request.session_id == session_id)
    ).scalars().all())
    counts = input_block_counts(db, session_id)
    ordered = sorted(rows, key=_sort_key)
    by_id = {r.id: r for r in ordered}
    position = {r.id: i for i, r in enumerate(ordered)}

    def members(group: dict, claimed: set[str]) -> list[Request]:
        ids = [rid for rid in group["request_ids"] if rid in by_id and rid not in claimed]
        claimed.update(ids)
        return [by_id[rid] for rid in sorted(ids, key=position.__getitem__)]

    claimed: set[str] = set()
    series: list[tuple[dict, bool, list[Request]]] = []
    for group in graph["conversations"]:
        series.append((group, False, members(group, claimed)))
    aux_group = graph.get("auxiliary")
    aux_members = members(aux_group, claimed) if aux_group else []
    leftover = [by_id[rid] for rid in sorted(by_id.keys() - claimed, key=position.__getitem__)]
    aux_all = sorted(aux_members + leftover, key=lambda r: position[r.id])

    out = [
        {"key": g["key"], "label": g["label"], "auxiliary": aux, "request_count": len(reqs),
         "points": [_point(r, position[r.id] + 1, counts) for r in reqs]}
        for g, aux, reqs in series
    ]
    if aux_all:
        out.append({
            "key": aux_group["key"] if aux_group else f"session:{session_id}:auxiliary",
            "label": aux_group["label"] if aux_group else "Auxiliary requests",
            "auxiliary": True, "request_count": len(aux_all),
            "points": [_point(r, position[r.id] + 1, counts) for r in aux_all],
        })
    return {
        "session_id": session_id,
        "metrics": [
            {"id": m.id, "label": m.label, "unit": m.unit, "estimated": m.estimated,
             "empty_hint": m.empty_hint, "kind": m.kind,
             "parts": [{"id": part_id, "label": label} for part_id, label, _column in m.parts]}
            for m in METRICS
        ],
        "series": out,
    }
