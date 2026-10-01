# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Build exact and conservatively inferred request lineage graphs."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.analysis.conversation_projection import _conversation_projection
from contextspy.analysis.context_diff import ContextBlock, ContextDelta, diff_contexts, new_child_block_ids, semantic_key
from contextspy.analysis.lineage_types import (
    LineageEdge, RequestSnapshot, input_blocks as _input_blocks,
    output_blocks as _output_blocks, request_order as _request_order,
)


ANALYSIS_VERSION = "lineage-v5-compaction-affinity"
INFERENCE_THRESHOLD = 0.80
AMBIGUOUS_THRESHOLD = 0.45
INFERENCE_MARGIN = 0.15
MAX_INFERENCE_CANDIDATES = 64
MAX_RETROSPECTIVE_LOOKAHEAD = 64
MAX_RETROSPECTIVE_DESCENDANTS = 3
FRONTIER_ANCESTOR_PENALTY = 0.20


@dataclass
class CandidateScore:
    request: RequestSnapshot
    score: float
    delta: ContextDelta
    reason_codes: list[str]
    retained_weight: float
    promoted_weight: float
    child_coverage: float
    frontier_position: tuple[int, int] | None = None
    frontier_coverage: float = 0.0
    promoted_keys: frozenset[tuple] = frozenset()
    decision_score: float | None = None

    def evidence(self, margin: float | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "reason_codes": self.reason_codes,
            "retained_weight": round(self.retained_weight, 4),
            "promoted_weight": round(self.promoted_weight, 4),
            "child_coverage": round(self.child_coverage, 4),
        }
        if self.frontier_position is not None:
            value["frontier_message_index"] = self.frontier_position[0]
            value["frontier_block_position"] = self.frontier_position[1]
            value["frontier_coverage"] = round(self.frontier_coverage, 4)
        if self.decision_score is not None and self.decision_score != self.score:
            value["comparison_score"] = round(self.decision_score, 4)
        if margin is not None:
            value["candidate_margin"] = round(margin, 4)
        return value


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


@dataclass(frozen=True)
class ScoringWeights:
    """Graph-local weights reused across candidate comparisons."""
    blocks: Mapping[int, float]  # object identity avoids assuming block IDs are globally unique
    input_totals: Mapping[str, float]
    output_totals: Mapping[str, float]


def _scoring_weights(
    requests: Iterable[RequestSnapshot], frequencies: Counter, document_count: int,
) -> ScoringWeights:
    blocks: dict[int, float] = {}
    input_totals: dict[str, float] = {}
    output_totals: dict[str, float] = {}
    for request in requests:
        input_total = output_total = 0.0
        for block in request.blocks:
            weight = _block_weight(block, frequencies, document_count)
            blocks[id(block)] = weight
            if block.direction == Direction.INPUT:
                input_total += weight
            elif block.direction == Direction.OUTPUT:
                output_total += weight
        input_totals[request.id] = input_total
        output_totals[request.id] = output_total
    return ScoringWeights(blocks, input_totals, output_totals)


def _score_candidate(
    parent: RequestSnapshot,
    child: RequestSnapshot,
    weights: ScoringWeights,
) -> CandidateScore:
    delta = diff_contexts(parent.blocks, child.blocks)
    parent_by_id = {block.id: block for block in parent.blocks}
    child_by_id = {block.id: block for block in child.blocks}

    retained_weight = sum(
        weights.blocks[id(parent_by_id[item.parent_block_id])]
        for item in delta.persisted
    )
    promoted_weight = sum(
        weights.blocks[id(parent_by_id[item.parent_block_id])]
        for item in delta.promoted
    )
    parent_input_weight = weights.input_totals[parent.id]
    parent_output_weight = weights.output_totals[parent.id]
    child_input_weight = weights.input_totals[child.id]

    retention = retained_weight / parent_input_weight if parent_input_weight else 0.0
    child_coverage = (
        (retained_weight + promoted_weight) / child_input_weight
        if child_input_weight else 0.0
    )
    promotion = (
        promoted_weight / parent_output_weight if parent_output_weight else 0.0
    )
    promoted_blocks = [child_by_id[item.child_block_id] for item in delta.promoted]
    frontier_position = max(
        ((block.message_index if block.message_index is not None else block.position,
          block.position) for block in promoted_blocks if block.is_meaningful),
        default=None,
    )
    promoted_keys = frozenset(
        key for item in delta.promoted
        if (key := semantic_key(parent_by_id[item.parent_block_id])) is not None
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
    cross_agent_unproven = bool(
        parent.agent and child.agent and parent.agent != child.agent
        and (promotion < 0.5 or not promoted_keys)
    )
    if cross_agent_unproven:
        score = min(score, INFERENCE_THRESHOLD - 0.01)

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
    if cross_agent_unproven:
        reasons.append("cross_agent_continuation_unproven")

    return CandidateScore(
        request=parent,
        score=max(0.0, min(score, 1.0)),
        delta=delta,
        reason_codes=reasons,
        retained_weight=retained_weight,
        promoted_weight=promoted_weight,
        child_coverage=child_coverage,
        frontier_position=frontier_position,
        frontier_coverage=promotion,
        promoted_keys=promoted_keys,
    )


def _is_ancestor(older_id: str, newer_id: str, parents: Mapping[str, str]) -> bool:
    current = newer_id
    seen: set[str] = set()
    while current in parents and current not in seen:
        seen.add(current)
        current = parents[current]
        if current == older_id:
            return True
    return False


def _frontier_dominates(
    newer: CandidateScore, older: CandidateScore, parents: Mapping[str, str],
) -> bool:
    """A later accepted ancestor turn, not merely its shared prefix, wins."""
    if (newer.frontier_position is None or older.frontier_position is None
            or newer.frontier_position <= older.frontier_position
            or newer.frontier_coverage < 0.5
            or newer.score < INFERENCE_THRESHOLD
            or newer.score < older.score - 0.05
            or newer.request.effective_started_at < older.request.timestamp):
        return False
    if not _is_ancestor(older.request.id, newer.request.id, parents):
        return False
    older_keys = {
        key for block in older.request.blocks
        if (key := semantic_key(block)) is not None
    }
    return bool(newer.promoted_keys - older_keys)


def _rank_candidates(
    candidates: list[CandidateScore], parents: Mapping[str, str],
) -> list[CandidateScore]:
    """Discount only ancestor rivals whose older output is visibly superseded."""
    for candidate in candidates:
        candidate.decision_score = candidate.score
    frontier_candidates = [item for item in candidates if item.frontier_position is not None]
    for older in frontier_candidates:
        if any(_frontier_dominates(newer, older, parents) for newer in frontier_candidates):
            older.decision_score = max(0.0, older.score - FRONTIER_ANCESTOR_PENALTY)
            older.reason_codes.append("ancestor_output_older")
    for newer in frontier_candidates:
        if any(_frontier_dominates(newer, older, parents) for older in frontier_candidates):
            newer.reason_codes.append("latest_turn_promoted")
    return sorted(candidates, key=lambda item: (
        -(item.decision_score if item.decision_score is not None else item.score),
        _request_order(item.request),
    ))


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
    scoring_weights = _scoring_weights(all_requests, frequencies, document_count)
    internal_by_id = {request.id: request for request in internal}
    order_by_id, context_postings = _candidate_ids_by_shared_context(internal)
    edges: list[LineageEdge] = []
    parents: dict[str, str] = {}
    parent_states: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    scored_by_child: dict[str, list[CandidateScore]] = {}

    # Provider references are fixed before heuristic decisions. They may point
    # outside the session; a missing explicit reference is never guessed around.
    for child in internal:
        if not child.predecessor_response_id:
            continue
        parent = response_map.get((child.provider, child.predecessor_response_id))
        if parent is None or _would_cycle(parent.id, child.id, parents):
            parent_states[child.id] = "unresolved_exact"
            diagnostic = {
                "request_id": child.id,
                "provider": child.provider,
                "predecessor_response_id": child.predecessor_response_id,
            }
            if parent is not None:
                diagnostic["reason"] = "cycle_rejected"
            unresolved.append(diagnostic)
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

    for index, child in enumerate(internal):
        if child.predecessor_response_id:
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
            score = _score_candidate(candidate, child, scoring_weights)
            if score.score >= AMBIGUOUS_THRESHOLD:
                candidates.append(score)
        candidates = _rank_candidates(candidates, parents)
        scored_by_child[child.id] = candidates

        if not candidates:
            parent_states[child.id] = "unavailable" if not child.blocks else "root"
            continue

        best = candidates[0]
        second_score = candidates[1].decision_score if len(candidates) > 1 else 0.0
        margin = (best.decision_score or 0.0) - (second_score or 0.0)
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
                evidence={**best.evidence(margin),
                          "raw_candidate_margin": round(
                              best.score - max((candidate.score for candidate in candidates[1:]),
                                               default=0.0), 4)},
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
                            (candidate.decision_score or 0.0) - (
                                (candidates[position + 1].decision_score or 0.0)
                                if position + 1 < len(candidates) else 0.0
                            )
                        ),
                    }
                    for position, candidate in enumerate(candidates[:3])
                ],
            })

    # A later *exact* successor can corroborate an earlier turn boundary. Only
    # reconsider close heuristic decisions, and only when all plausible rivals
    # are older ancestors whose output is visibly earlier in that successor.
    exact_children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.certainty == "exact" and edge.source_request_id in internal_ids:
            exact_children[edge.source_request_id].append(edge.target_request_id)
    for child in internal:
        if parent_states.get(child.id) not in ("ambiguous", "inferred"):
            continue
        candidates = scored_by_child.get(child.id, [])
        if len(candidates) < 2 or len(candidates) > 8:
            continue
        queue = list(exact_children.get(child.id, ()))
        descendants: list[str] = []
        while queue and len(descendants) < MAX_RETROSPECTIVE_DESCENDANTS:
            descendant_id = queue.pop(0)
            if order_by_id[descendant_id] - order_by_id[child.id] > MAX_RETROSPECTIVE_LOOKAHEAD:
                continue
            descendants.append(descendant_id)
            queue.extend(exact_children.get(descendant_id, ()))
        if not descendants:
            continue
        plausible = [item for item in candidates if item.score >= INFERENCE_THRESHOLD]
        if len(plausible) < 2:
            continue
        chosen: CandidateScore | None = None
        confirming_id: str | None = None
        for descendant_id in descendants:
            descendant = internal_by_id[descendant_id]
            if descendant.context_fidelity == "opaque":
                continue
            future_scores = {
                item.request.id: _score_candidate(
                    item.request, descendant, scoring_weights,
                ) for item in plausible
            }
            winners = [
                item for item in plausible
                if item.score >= INFERENCE_THRESHOLD
                and item.frontier_coverage >= 0.5
                and future_scores[item.request.id].frontier_coverage >= 0.5
                and all(
                    item.request.id == rival.request.id or
                    (
                        _frontier_dominates(item, rival, parents)
                        and _frontier_dominates(
                            future_scores[item.request.id],
                            future_scores[rival.request.id], parents,
                        )
                    ) for rival in plausible
                )
            ]
            if len(winners) == 1:
                chosen = winners[0]
                confirming_id = descendant_id
                break
        if chosen is None or confirming_id is None:
            continue
        previous_parent = parents.pop(child.id, None)
        if _would_cycle(chosen.request.id, child.id, parents):
            if previous_parent is not None:
                parents[child.id] = previous_parent
            continue
        parents[child.id] = chosen.request.id
        parent_states[child.id] = "inferred"
        edges = [edge for edge in edges if not (
            edge.target_request_id == child.id and edge.certainty == "inferred"
        )]
        evidence = chosen.evidence()
        evidence["reason_codes"] = list(dict.fromkeys(
            [*evidence["reason_codes"], "retrospective_exact_descendant"]
        ))
        evidence["retrospective_descendant_request_id"] = confirming_id
        edges.append(LineageEdge(
            source_request_id=chosen.request.id,
            target_request_id=child.id,
            certainty="inferred",
            confidence=round(chosen.score, 4),
            evidence_source="context_diff",
            evidence=evidence,
            delta=chosen.delta,
        ))
        ambiguous = [item for item in ambiguous if item["request_id"] != child.id]

    for edge in edges:
        parent = request_by_id[edge.source_request_id]
        child = request_by_id[edge.target_request_id]
        if (parent.stream_hint_source and child.stream_hint_source
                and parent.stream_hint_source == child.stream_hint_source
                and parent.stream_hint_digest and child.stream_hint_digest
                and parent.stream_hint_digest != child.stream_hint_digest):
            edge.evidence.setdefault("reason_codes", []).append("hint_conflict")

    graph_requests = internal + [
        request for request in external
        if any(edge.source_request_id == request.id for edge in edges)
    ]
    graph_requests.sort(key=_request_order)
    topology = _assign_topology(graph_requests, edges)
    projection = _conversation_projection(internal, edges, parent_states, ambiguous)
    confirmed_fork_parents = {
        group["fork_parent_request_id"] for group in projection["conversations"][1:]
        if group["evidence"] == "fork" and group["fork_parent_request_id"]
    }
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
        node["conversation_fork_status"] = (
            "confirmed" if request.id in confirmed_fork_parents else
            "unconfirmed_graph_branch" if node["is_fork"] else "none"
        )
        nodes.append(node)

    for path in projection["lineage_paths"]:
        root = internal_by_id[path["request_ids"][0]]
        leaf = internal_by_id[path["leaf_request_id"]]
        path.update({
            "key": path["leaf_request_id"],
            "root_request_id": root.id,
            "root_session_seq": root.session_seq,
            "leaf_session_seq": leaf.session_seq,
            "request_count": len(path["request_ids"]),
            "last_activity": leaf.timestamp.isoformat(),
            "start_parent_state": parent_states[root.id],
        })

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
    delta = diff_contexts(parent.blocks, child.blocks)
    return {
        "parent_request_id": parent.id,
        "child_request_id": child.id,
        "delta": delta.to_dict(include_mappings=True),
        "new_child_block_ids": new_child_block_ids(delta, child.blocks),
    }
