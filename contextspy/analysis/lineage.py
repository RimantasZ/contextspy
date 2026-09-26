# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Build exact and conservatively inferred request lineage graphs."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.analysis.context_diff import ContextBlock, ContextDelta, diff_contexts, semantic_key


ANALYSIS_VERSION = "lineage-v2"
INFERENCE_THRESHOLD = 0.80
AMBIGUOUS_THRESHOLD = 0.45
INFERENCE_MARGIN = 0.15
MAX_INFERENCE_CANDIDATES = 64


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
class CandidateScore:
    request: RequestSnapshot
    score: float
    delta: ContextDelta
    reason_codes: list[str]
    retained_weight: float
    promoted_weight: float
    child_coverage: float

    def evidence(self, margin: float | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "reason_codes": self.reason_codes,
            "retained_weight": round(self.retained_weight, 4),
            "promoted_weight": round(self.promoted_weight, 4),
            "child_coverage": round(self.child_coverage, 4),
        }
        if margin is not None:
            value["candidate_margin"] = round(margin, 4)
        return value


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


def _request_order(request: RequestSnapshot) -> tuple[datetime, datetime, int, str]:
    return (
        request.effective_started_at,
        request.timestamp,
        request.session_seq if request.session_seq is not None else 2**31,
        request.id,
    )


def _input_blocks(request: RequestSnapshot) -> list[ContextBlock]:
    return [block for block in request.blocks if block.direction == Direction.INPUT]


def _output_blocks(request: RequestSnapshot) -> list[ContextBlock]:
    return [block for block in request.blocks if block.direction == Direction.OUTPUT]


def _document_frequencies(requests: Iterable[RequestSnapshot]) -> tuple[Counter, int]:
    frequencies: Counter = Counter()
    count = 0
    for request in requests:
        count += 1
        keys = {
            semantic_key(block)
            for block in _input_blocks(request)
            if semantic_key(block) is not None
        }
        frequencies.update(keys)
    return frequencies, max(count, 1)


def _candidate_ids_by_shared_context(
    requests: list[RequestSnapshot],
) -> tuple[dict[str, int], dict[tuple, list[str]]]:
    """Index meaningful transcript/output fingerprints for bounded retrieval."""
    order = {request.id: index for index, request in enumerate(requests)}
    postings: dict[tuple, list[str]] = defaultdict(list)
    for request in requests:
        keys = {
            semantic_key(block)
            for block in request.blocks
            if not block.is_configuration and semantic_key(block) is not None
        }
        for key in keys:
            postings[key].append(request.id)
    return order, postings


def _block_weight(block: ContextBlock, frequencies: Counter, document_count: int) -> float:
    key = semantic_key(block)
    if key is None:
        return 0.0
    idf = math.log((document_count + 1) / (frequencies.get(key, 0) + 1)) + 1.0
    if block.block_type in (BlockType.SYSTEM_PROMPT, BlockType.TOOL_DEFINITION):
        return idf * 0.08
    if block.tool_call_id:
        return idf * 1.35
    return idf


def _score_candidate(
    parent: RequestSnapshot,
    child: RequestSnapshot,
    frequencies: Counter,
    document_count: int,
) -> CandidateScore:
    delta = diff_contexts(parent.blocks, child.blocks)
    parent_by_id = {block.id: block for block in parent.blocks}
    child_by_id = {block.id: block for block in child.blocks}

    retained_weight = sum(
        _block_weight(parent_by_id[item.parent_block_id], frequencies, document_count)
        for item in delta.persisted
    )
    promoted_weight = sum(
        _block_weight(parent_by_id[item.parent_block_id], frequencies, document_count)
        for item in delta.promoted
    )
    parent_input_weight = sum(
        _block_weight(block, frequencies, document_count)
        for block in _input_blocks(parent)
    )
    parent_output_weight = sum(
        _block_weight(block, frequencies, document_count)
        for block in _output_blocks(parent)
    )
    child_input_weight = sum(
        _block_weight(block, frequencies, document_count)
        for block in _input_blocks(child)
    )

    retention = retained_weight / parent_input_weight if parent_input_weight else 0.0
    child_coverage = (
        (retained_weight + promoted_weight) / child_input_weight
        if child_input_weight else 0.0
    )
    promotion = (
        promoted_weight / parent_output_weight if parent_output_weight else 0.0
    )
    if parent_output_weight:
        score = 0.35 * retention + 0.45 * child_coverage + 0.20 * promotion
    else:
        score = 0.45 * retention + 0.55 * child_coverage

    strong_persisted = any(
        not parent_by_id[item.parent_block_id].is_configuration
        for item in delta.persisted
    )
    matching_tool_ids = {
        parent_by_id[item.parent_block_id].tool_call_id
        for item in delta.persisted + delta.promoted
        if parent_by_id[item.parent_block_id].tool_call_id
    }
    strong_signal = strong_persisted or promoted_weight > 0 or bool(matching_tool_ids)
    if not strong_signal:
        score = min(score, 0.30)
    if child.context_fidelity in ("partial", "opaque"):
        score *= 0.92

    reasons: list[str] = []
    if retained_weight:
        reasons.append("ordered_context_retained")
    if promoted_weight:
        reasons.append("parent_output_promoted")
    if matching_tool_ids:
        reasons.append("tool_call_ids_match")
    if delta.replaced:
        reasons.append("configuration_replaced")
    if child.context_fidelity in ("partial", "opaque"):
        reasons.append(f"child_context_{child.context_fidelity}")
    if not strong_signal:
        reasons.append("boilerplate_only")

    return CandidateScore(
        request=parent,
        score=max(0.0, min(score, 1.0)),
        delta=delta,
        reason_codes=reasons,
        retained_weight=retained_weight,
        promoted_weight=promoted_weight,
        child_coverage=child_coverage,
    )


def _would_cycle(parent_id: str, child_id: str, parents: Mapping[str, str]) -> bool:
    current = parent_id
    seen = {child_id}
    while current in parents:
        if current in seen:
            return True
        seen.add(current)
        current = parents[current]
    return current in seen


def _assign_topology(
    requests: list[RequestSnapshot], edges: list[LineageEdge],
) -> dict[str, dict[str, Any]]:
    request_by_id = {request.id: request for request in requests}
    primary_parent = {
        edge.target_request_id: edge.source_request_id
        for edge in edges
        if edge.relation_type == "context_continuation"
    }
    children: dict[str, list[str]] = defaultdict(list)
    for child_id, parent_id in primary_parent.items():
        children[parent_id].append(child_id)
    for values in children.values():
        values.sort(key=lambda request_id: _request_order(request_by_id[request_id]))

    roots = [request for request in requests if request.id not in primary_parent]
    roots.sort(key=_request_order)
    topology: dict[str, dict[str, Any]] = {}
    next_branch_by_lineage: dict[int, int] = defaultdict(lambda: 1)

    for lineage_number, root in enumerate(roots, start=1):
        stack = [(root.id, 0, 0)]
        while stack:
            request_id, depth, branch = stack.pop()
            topology[request_id] = {
                "lineage_key": root.id,
                "lineage_number": lineage_number,
                "depth": depth,
                "branch": branch,
                "is_fork": len(children.get(request_id, [])) > 1,
            }
            child_entries: list[tuple[str, int, int]] = []
            for index, child_id in enumerate(children.get(request_id, [])):
                child_branch = branch
                if index > 0:
                    child_branch = next_branch_by_lineage[lineage_number]
                    next_branch_by_lineage[lineage_number] += 1
                child_entries.append((child_id, depth + 1, child_branch))
            stack.extend(reversed(child_entries))
    return topology


def _conversation_projection(
    internal: list[RequestSnapshot], edges: list[LineageEdge],
    parent_states: Mapping[str, str], ambiguous: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep diagnostic paths separate from positively evidenced display streams.

    A missing/ambiguous parent is a fragment, not evidence of another conversation.
    No provider currently persists a documented task/thread ID, so that optional
    source of stream identity is intentionally unavailable here.
    """
    if not internal:
        return {"lineage_paths": [], "lineage_fragment_count": 0,
                "conversation_count": 0, "confirmed_parallel_streams": 0,
                "conversations": [], "membership": {}}

    by_id = {request.id: request for request in internal}
    parent_edge = {edge.target_request_id: edge for edge in edges
                   if edge.relation_type == "context_continuation"}
    children: dict[str, list[str]] = defaultdict(list)
    fork_children: dict[str, list[str]] = defaultdict(list)
    for child_id, edge in parent_edge.items():
        fork_children[edge.source_request_id].append(child_id)
        if edge.source_request_id in by_id:
            children[edge.source_request_id].append(child_id)
    for values in children.values():
        values.sort(key=lambda rid: _request_order(by_id[rid]))

    paths: list[list[str]] = []
    for request in internal:
        if children.get(request.id):
            continue
        path = [request.id]
        current = request.id
        while current in parent_edge and parent_edge[current].source_request_id in by_id:
            current = parent_edge[current].source_request_id
            path.append(current)
        paths.append(list(reversed(path)))
    paths.sort(key=lambda path: (_request_order(by_id[path[-1]]), path[-1]))
    path_sets = [set(path) for path in paths]
    evidence: dict[int, dict[str, Any]] = {}
    fingerprints = {
        request.id: {
            key for block in request.blocks
            if not block.is_configuration
            if (key := semantic_key(block)) is not None
        }
        for request in internal
    }

    # A fork needs two genuinely active branches: overlapping observed calls,
    # or accepted continuation after *both* immediate children.
    for parent_id, child_ids in fork_children.items():
        if len(child_ids) < 2:
            continue
        for left_index, left_id in enumerate(child_ids):
            for right_id in child_ids[left_index + 1:]:
                left, right = by_id[left_id], by_id[right_id]
                observed_overlap = (
                    left.started_at is not None and right.started_at is not None
                    and left.started_at < right.timestamp
                    and right.started_at < left.timestamp
                )
                left_keys, right_keys = fingerprints[left_id], fingerprints[right_id]
                distinct_context = bool(left_keys and right_keys and left_keys != right_keys)
                sustained = bool(children.get(left_id)) and bool(children.get(right_id))
                # A non-overlapping inferred sibling can be a misplaced
                # continuation after compaction. Do not promote it to a new
                # stream solely because both later acquired descendants.
                exact_sustained = (
                    sustained and parent_edge[left_id].certainty == "exact"
                    and parent_edge[right_id].certainty == "exact"
                )
                if not (exact_sustained or (observed_overlap and distinct_context)):
                    continue
                for child_id in (left_id, right_id):
                    for index, path in enumerate(paths):
                        if child_id in path:
                            evidence.setdefault(index, {
                                "evidence": "fork", "fork_parent_request_id": parent_id,
                                "anchor_request_id": child_id,
                            })

    # Without a task ID, require sustained interleaving of *observed* starts.
    # No matching non-configuration fingerprints is deliberately stricter than
    # the ambiguous-candidate threshold, and avoids claiming opaque fragments.
    ambiguous_pairs = {
        (item["request_id"], candidate["request_id"])
        for item in ambiguous for candidate in item.get("candidates", [])
    }
    for left_index, left_path in enumerate(paths):
        if len(left_path) < 3 or not all(by_id[rid].started_at for rid in left_path):
            continue
        left_keys = set().union(*(fingerprints[rid] for rid in left_path))
        if not left_keys:
            continue
        for right_index in range(left_index + 1, len(paths)):
            right_path = paths[right_index]
            if len(right_path) < 3 or path_sets[left_index] & path_sets[right_index]:
                continue
            # Paths rooted in the same captured external predecessor are fork
            # candidates, never independent roots merely because the external
            # node is excluded from in-session paths.
            left_parent = parent_edge.get(left_path[0])
            right_parent = parent_edge.get(right_path[0])
            if (left_parent and right_parent
                    and left_parent.source_request_id == right_parent.source_request_id):
                continue
            if not all(by_id[rid].started_at for rid in right_path):
                continue
            right_keys = set().union(*(fingerprints[rid] for rid in right_path))
            if not right_keys or left_keys & right_keys:
                continue
            if any((a, b) in ambiguous_pairs or (b, a) in ambiguous_pairs
                   for a in left_path for b in right_path):
                continue
            starts = sorted(
                [(by_id[rid].started_at, "A") for rid in left_path]
                + [(by_id[rid].started_at, "B") for rid in right_path]
            )
            labels = [label for _, label in starts]
            if not any(labels[i:i + 4] in (["A", "B", "A", "B"], ["B", "A", "B", "A"])
                       for i in range(len(labels) - 3)):
                continue
            for index in (left_index, right_index):
                evidence.setdefault(index, {
                    "evidence": "parallel_chains",
                    "anchor_request_id": paths[index][0],
                })

    def rank(index: int) -> tuple[int, int, datetime, str]:
        path = paths[index]
        last = by_id[path[-1]]
        return (len(path), last.session_seq or -1, last.timestamp, last.id)

    primary_index = max(evidence or range(len(paths)), key=rank)
    primary_path = paths[primary_index]
    confirmed = sorted((index for index in evidence if index != primary_index),
                       key=rank, reverse=True)
    secondary_ids = set().union(*(path_sets[index] - path_sets[primary_index]
                                  for index in confirmed)) if confirmed else set()
    primary_ids = set(by_id) - secondary_ids
    session_id = internal[0].session_id or "unassigned"
    groups = [{
        "key": f"session:{session_id}:primary",
        "label": "Session request sequence" if not confirmed else "Primary conversation",
        "evidence": evidence.get(primary_index, {}).get("evidence", "default"),
        "fork_parent_request_id": evidence.get(primary_index, {}).get("fork_parent_request_id"),
        "request_ids": sorted(primary_ids, key=lambda rid: _request_order(by_id[rid])),
        "confirmed_request_ids": primary_path if len(primary_path) > 1 else [],
    }]
    for index in confirmed:
        proof = evidence[index]
        groups.append({
            "key": f"session:{session_id}:{proof['evidence']}:{proof['anchor_request_id']}",
            "label": f"Conversation {len(groups) + 1}",
            "evidence": proof["evidence"],
            "fork_parent_request_id": proof.get("fork_parent_request_id"),
            "request_ids": paths[index],
            "confirmed_request_ids": paths[index],
        })
    membership: dict[str, list[dict[str, str]]] = defaultdict(list)
    for group in groups:
        confirmed_ids = set(group["confirmed_request_ids"])
        for rid in group["request_ids"]:
            membership[rid].append({
                "key": group["key"],
                "state": "confirmed" if rid in confirmed_ids else "unassigned",
            })
    return {
        "lineage_paths": [{"request_ids": path, "leaf_request_id": path[-1]}
                          for path in paths],
        "lineage_fragment_count": len(paths),
        "conversation_count": len(groups),
        "confirmed_parallel_streams": len(groups) - 1,
        "conversations": groups,
        "membership": dict(membership),
    }


def build_lineage_graph(
    requests: Iterable[RequestSnapshot],
    *,
    external_requests: Iterable[RequestSnapshot] = (),
) -> dict[str, Any]:
    """Build a deterministic graph from exact IDs and conservative block inference."""
    internal = sorted(list(requests), key=_request_order)
    external = list(external_requests)
    all_requests = internal + external
    request_by_id = {request.id: request for request in all_requests}
    internal_ids = {request.id for request in internal}

    response_map: dict[tuple[str, str], RequestSnapshot] = {}
    for request in sorted(all_requests, key=lambda item: (item.timestamp, item.id), reverse=True):
        if request.provider_response_id:
            response_map.setdefault(
                (request.provider, request.provider_response_id), request,
            )

    frequencies, document_count = _document_frequencies(internal)
    internal_by_id = {request.id: request for request in internal}
    order_by_id, context_postings = _candidate_ids_by_shared_context(internal)
    edges: list[LineageEdge] = []
    parents: dict[str, str] = {}
    parent_states: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for index, child in enumerate(internal):
        if child.predecessor_response_id:
            parent = response_map.get((child.provider, child.predecessor_response_id))
            if parent is None:
                parent_states[child.id] = "unresolved_exact"
                unresolved.append({
                    "request_id": child.id,
                    "provider": child.provider,
                    "predecessor_response_id": child.predecessor_response_id,
                })
                continue
            if _would_cycle(parent.id, child.id, parents):
                parent_states[child.id] = "unresolved_exact"
                unresolved.append({
                    "request_id": child.id,
                    "provider": child.provider,
                    "predecessor_response_id": child.predecessor_response_id,
                    "reason": "cycle_rejected",
                })
                continue
            delta = (
                diff_contexts(parent.blocks, child.blocks)
                if parent.blocks and child.blocks else None
            )
            edges.append(LineageEdge(
                source_request_id=parent.id,
                target_request_id=child.id,
                certainty="exact",
                evidence_source="provider",
                evidence={
                    "reason_codes": ["provider_predecessor_id"],
                    "predecessor_response_id": child.predecessor_response_id,
                },
                delta=delta,
                external_source=parent.id not in internal_ids,
            ))
            parents[child.id] = parent.id
            parent_states[child.id] = "exact"
            continue

        child_keys = {
            semantic_key(block)
            for block in _input_blocks(child)
            if not block.is_configuration and semantic_key(block) is not None
        }
        overlaps: Counter[str] = Counter()
        for key in child_keys:
            overlaps.update(
                request_id for request_id in context_postings.get(key, ())
                if order_by_id[request_id] < index
            )
        candidate_requests = [
            internal_by_id[request_id]
            for request_id, _ in sorted(
                overlaps.items(),
                key=lambda item: (-item[1], -order_by_id[item[0]], item[0]),
            )[:MAX_INFERENCE_CANDIDATES]
        ]

        candidates: list[CandidateScore] = []
        for candidate in candidate_requests:
            if not candidate.blocks or not child.blocks:
                continue
            score = _score_candidate(candidate, child, frequencies, document_count)
            if score.score >= AMBIGUOUS_THRESHOLD:
                candidates.append(score)
        candidates.sort(key=lambda item: (-item.score, _request_order(item.request)))

        if not candidates:
            parent_states[child.id] = "unavailable" if not child.blocks else "root"
            continue

        best = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        margin = best.score - second_score
        if best.score >= INFERENCE_THRESHOLD and margin >= INFERENCE_MARGIN:
            if _would_cycle(best.request.id, child.id, parents):
                parent_states[child.id] = "ambiguous"
                ambiguous.append({
                    "request_id": child.id,
                    "reason": "cycle_rejected",
                    "candidates": [],
                })
                continue
            edges.append(LineageEdge(
                source_request_id=best.request.id,
                target_request_id=child.id,
                certainty="inferred",
                confidence=round(best.score, 4),
                evidence_source="context_diff",
                evidence=best.evidence(margin),
                delta=best.delta,
            ))
            parents[child.id] = best.request.id
            parent_states[child.id] = "inferred"
        else:
            parent_states[child.id] = "ambiguous"
            ambiguous.append({
                "request_id": child.id,
                "candidates": [
                    {
                        "request_id": candidate.request.id,
                        "confidence": round(candidate.score, 4),
                        "evidence": candidate.evidence(
                            candidate.score - (
                                candidates[position + 1].score
                                if position + 1 < len(candidates) else 0.0
                            )
                        ),
                    }
                    for position, candidate in enumerate(candidates[:3])
                ],
            })

    graph_requests = internal + [
        request for request in external
        if any(edge.source_request_id == request.id for edge in edges)
    ]
    graph_requests.sort(key=_request_order)
    topology = _assign_topology(graph_requests, edges)
    projection = _conversation_projection(internal, edges, parent_states, ambiguous)
    nodes = []
    for request in graph_requests:
        node = {
            "request_id": request.id,
            "session_id": request.session_id,
            "session_seq": request.session_seq,
            "started_at": (
                request.started_at.isoformat() if request.started_at is not None
                else request.effective_started_at.isoformat()
            ),
            "started_at_source": request.started_at_source,
            "completed_at": request.timestamp.isoformat(),
            "duration_ms": request.duration_ms,
            "provider": request.provider,
            "agent": request.agent,
            "model": request.model,
            "endpoint": request.endpoint,
            "context_fidelity": request.context_fidelity,
            "tokens_total_input": request.tokens_total_input,
            "tokens_total_output": request.tokens_total_output,
            "provider_response_id": request.provider_response_id,
            "predecessor_response_id": request.predecessor_response_id,
            "external": request.external,
            "parent_state": parent_states.get(request.id, "external" if request.external else "root"),
            "conversation_membership": projection["membership"].get(request.id, []),
            "parent_request_id": parents.get(request.id),
            **topology.get(request.id, {
                "lineage_key": request.id,
                "lineage_number": 0,
                "depth": 0,
                "branch": 0,
                "is_fork": False,
            }),
        }
        nodes.append(node)

    edges.sort(key=lambda edge: (
        _request_order(request_by_id[edge.target_request_id]),
        edge.relation_type,
        edge.source_request_id,
    ))
    return {
        "analysis_version": ANALYSIS_VERSION,
        "nodes": nodes,
        "edges": [edge.to_dict() for edge in edges],
        "unresolved_predecessors": unresolved,
        "ambiguous_candidates": ambiguous,
        **{key: value for key, value in projection.items() if key != "membership"},
    }


def context_diff_for_requests(
    parent: RequestSnapshot, child: RequestSnapshot,
) -> dict[str, Any]:
    return {
        "parent_request_id": parent.id,
        "child_request_id": child.id,
        "delta": diff_contexts(parent.blocks, child.blocks).to_dict(include_mappings=True),
    }
