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


ANALYSIS_VERSION = "lineage-v4-retrospective-streams"
INFERENCE_THRESHOLD = 0.80
AMBIGUOUS_THRESHOLD = 0.45
INFERENCE_MARGIN = 0.15
MAX_INFERENCE_CANDIDATES = 64
MAX_AFFINITY_CANDIDATES = 32
MAX_AFFINITY_POSTINGS = 64
MAX_RETROSPECTIVE_LOOKAHEAD = 64
MAX_RETROSPECTIVE_DESCENDANTS = 3
FRONTIER_ANCESTOR_PENALTY = 0.20


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
    if not _is_ancestor(older.request.id, newer.request.id, parents):
        return False
    if (newer.frontier_position is None or older.frontier_position is None
            or newer.frontier_position <= older.frontier_position
            or newer.frontier_coverage < 0.5
            or newer.score < INFERENCE_THRESHOLD
            or newer.score < older.score - 0.05
            or newer.request.effective_started_at < older.request.timestamp):
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
    for older in candidates:
        if any(_frontier_dominates(newer, older, parents) for newer in candidates):
            older.decision_score = max(0.0, older.score - FRONTIER_ANCESTOR_PENALTY)
            older.reason_codes.append("ancestor_output_older")
    for newer in candidates:
        if any(_frontier_dominates(newer, older, parents) for older in candidates):
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


def _strong_stream_context(
    left: Mapping[tuple, int], right: Mapping[tuple, int],
) -> bool:
    """Substantive retained transcript, excluding configuration boilerplate."""
    if len(left) < 3 or len(right) < 3:
        return False
    shared = left.keys() & right.keys()
    if len(shared) < 3 or len(shared) / min(len(left), len(right)) < 0.70:
        return False
    shared_weight = sum(min(left[key], right[key]) for key in shared)
    smaller_weight = min(sum(left.values()), sum(right.values()))
    return shared_weight >= 128 and smaller_weight > 0 and shared_weight / smaller_weight >= 0.70


def _stream_affinity_groups(
    internal: list[RequestSnapshot], edges: list[LineageEdge],
    primary_path: list[str], primary_ids: set[str], next_number: int,
) -> tuple[list[dict[str, Any]], set[str], dict[str, dict[str, Any]]]:
    """Promote corroborated Codex streams without modifying lineage edges.

    Exact/inferred parents establish components. A matching cache-affinity
    hint plus strong non-configuration context can join components for display
    only. Distinct hints need an independently accepted chain to make a new
    group; transient single calls never suffice.
    """
    by_id = {request.id: request for request in internal}
    hint = {
        request.id: (request.stream_hint_source, request.stream_hint_digest)
        if request.stream_hint_source and request.stream_hint_digest else None
        for request in internal
    }
    base_confirmed = set(primary_path) if len(primary_path) > 1 else set()
    if not any(hint.values()):
        return [], base_confirmed, {}

    request_parent = {rid: rid for rid in by_id}
    def find_request(rid: str) -> str:
        while request_parent[rid] != rid:
            request_parent[rid] = request_parent[request_parent[rid]]
            rid = request_parent[rid]
        return rid
    def join_requests(left: str, right: str) -> None:
        request_parent[find_request(right)] = find_request(left)

    accepted_internal = [edge for edge in edges
                         if edge.relation_type == "context_continuation"
                         and edge.source_request_id in by_id]
    for edge in accepted_internal:
        join_requests(edge.source_request_id, edge.target_request_id)

    hint_parent = {value: value for value in hint.values() if value}
    def find_hint(value: tuple[str, str]) -> tuple[str, str]:
        while hint_parent[value] != value:
            hint_parent[value] = hint_parent[hint_parent[value]]
            value = hint_parent[value]
        return value
    def join_hints(left: tuple[str, str], right: tuple[str, str]) -> None:
        hint_parent[find_hint(right)] = find_hint(left)

    hints_by_component: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for rid, value in hint.items():
        if value:
            hints_by_component[find_request(rid)].add(value)
    for values in hints_by_component.values():
        if len(values) > 1:
            first = min(values)
            for value in sorted(values):
                join_hints(first, value)
    canonical_hint = {rid: find_hint(value) if value else None
                      for rid, value in hint.items()}

    weights: dict[str, dict[tuple, int]] = {}
    for request in internal:
        values: dict[tuple, int] = {}
        for block in request.blocks:
            if block.direction != Direction.INPUT or block.is_configuration:
                continue
            key = semantic_key(block)
            if key is not None:
                values[key] = max(values.get(key, 0), max(1, block.token_count))
        weights[request.id] = values

    postings: dict[tuple, list[str]] = defaultdict(list)
    bridges: dict[str, dict[str, Any]] = {}
    accepted_children = {edge.target_request_id for edge in edges
                         if edge.relation_type == "context_continuation"}
    for request in internal:
        rid = request.id
        value = canonical_hint[rid]
        if value is None or not weights[rid]:
            continue
        overlaps: Counter[str] = Counter()
        for key in weights[rid]:
            overlaps.update(postings[(value, key)][-MAX_AFFINITY_POSTINGS:])
        candidates = sorted(
            overlaps, key=lambda candidate: (overlaps[candidate], _request_order(by_id[candidate])),
            reverse=True,
        )[:MAX_AFFINITY_CANDIDATES]
        for candidate in candidates:
            if find_request(candidate) == find_request(rid):
                continue
            if not _strong_stream_context(weights[candidate], weights[rid]):
                continue
            join_requests(candidate, rid)
            if rid not in accepted_children:
                bridges[rid] = {
                    "prior_request_id": candidate,
                    "evidence": "context_affinity",
                    "parent_edge": False,
                }
            break
        for key in weights[rid]:
            postings[(value, key)].append(rid)

    members: dict[str, set[str]] = defaultdict(set)
    for rid in by_id:
        members[find_request(rid)].add(rid)
    anchor_root = find_request(primary_path[-1])
    anchor_members = members[anchor_root]
    anchor_hints = Counter(canonical_hint[rid] for rid in anchor_members if canonical_hint[rid])
    if not anchor_hints:
        return [], base_confirmed, bridges
    anchor_hint = min(anchor_hints, key=lambda value: (-anchor_hints[value], value))
    anchor_latest = max(anchor_members, key=lambda rid: _request_order(by_id[rid]))
    if (len(weights[anchor_latest]) < 3
            or sum(weights[anchor_latest].values()) < 128):
        return [], base_confirmed | (anchor_members & primary_ids), bridges

    accepted_counts: Counter[str] = Counter()
    for edge in accepted_internal:
        accepted_counts[find_request(edge.target_request_id)] += 1
    candidates_by_hint: dict[tuple[str, str], list[tuple[str, set[str]]]] = defaultdict(list)
    for root, ids in members.items():
        if root == anchor_root or not ids <= primary_ids or accepted_counts[root] == 0:
            continue
        values = Counter(canonical_hint[rid] for rid in ids if canonical_hint[rid])
        if not values:
            continue
        value = min(values, key=lambda candidate: (-values[candidate], candidate))
        if value == anchor_hint:
            continue
        latest = max(ids, key=lambda rid: _request_order(by_id[rid]))
        if (len(weights[latest]) < 3
                or sum(weights[latest].values()) < 128):
            continue
        if (_strong_stream_context(weights[anchor_latest], weights[latest])
                or _strong_stream_context(weights[latest], weights[anchor_latest])):
            continue
        candidates_by_hint[value].append((root, ids))

    session_id = internal[0].session_id or "unassigned"
    groups: list[dict[str, Any]] = []
    for components in candidates_by_hint.values():
        _, ids = max(components, key=lambda item: (
            len(item[1]), max(_request_order(by_id[rid]) for rid in item[1]),
        ))
        anchor = min(ids, key=lambda rid: _request_order(by_id[rid]))
        groups.append({
            "key": f"session:{session_id}:stream:{anchor}",
            "label": f"Conversation {next_number + len(groups)}",
            "evidence": "stream_affinity",
            "fork_parent_request_id": None,
            "request_ids": sorted(ids, key=lambda rid: _request_order(by_id[rid])),
            "confirmed_request_ids": sorted(ids, key=lambda rid: _request_order(by_id[rid])),
        })
    return groups, base_confirmed | (anchor_members & primary_ids), bridges


def _finalize_activity_groups(
    internal: list[RequestSnapshot], edges: list[LineageEdge],
    groups: list[dict[str, Any]], bridges: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Separate supported streams from provisional requests after graph analysis.

    Existing confirmed forks/parallel chains/cache-affinity groups are retained.
    The formerly catch-all primary group is partitioned into evidence-connected
    components; a short or uncorroborated component goes to Auxiliary requests.
    """
    by_id = {request.id: request for request in internal}
    primary = groups[0]
    primary_ids = set(primary["request_ids"])
    component_parent = {rid: rid for rid in primary_ids}

    def find(rid: str) -> str:
        while component_parent[rid] != rid:
            component_parent[rid] = component_parent[component_parent[rid]]
            rid = component_parent[rid]
        return rid

    def join(left: str, right: str) -> None:
        component_parent[find(right)] = find(left)

    for edge in edges:
        if edge.source_request_id in primary_ids and edge.target_request_id in primary_ids:
            join(edge.source_request_id, edge.target_request_id)
    for rid, bridge in bridges.items():
        prior = bridge["prior_request_id"]
        if rid in primary_ids and prior in primary_ids:
            join(prior, rid)

    components: dict[str, set[str]] = defaultdict(set)
    for rid in primary_ids:
        components[find(rid)].add(rid)
    if not components:
        return groups[1:], None

    weights: dict[str, dict[tuple, int]] = {}
    for request in internal:
        values: dict[tuple, int] = {}
        for block in _input_blocks(request):
            if block.is_configuration:
                continue
            key = semantic_key(block)
            if key is not None:
                values[key] = max(values.get(key, 0), max(1, block.token_count))
        weights[request.id] = values

    def ordered_ids(ids: set[str]) -> list[str]:
        return sorted(ids, key=lambda rid: _request_order(by_id[rid]))

    def representatives(ids: set[str]) -> list[str]:
        ordered = ordered_ids(ids)
        return list(dict.fromkeys([*ordered[:1], *ordered[-3:]]))

    def distinctive(left: set[str], right: set[str]) -> bool:
        """Adequate recent context with no overlap to recent or anchor context."""
        left_latest = max(left, key=lambda rid: _request_order(by_id[rid]))
        right_latest = max(right, key=lambda rid: _request_order(by_id[rid]))
        a, b = weights[left_latest], weights[right_latest]
        def adequate(rid: str, values: Mapping[tuple, int]) -> bool:
            # Opaque/partial canonical context can still retain many observed
            # fingerprints; demand much more material before treating absence
            # of overlap as negative evidence.
            fidelity = by_id[rid].context_fidelity
            count_floor, weight_floor = (
                (10, 512) if fidelity == "opaque" else
                (5, 256) if fidelity == "partial" else (3, 128)
            )
            return len(values) >= count_floor and sum(values.values()) >= weight_floor
        return (
            adequate(left_latest, a) and adequate(right_latest, b)
            and not any(
                weights[left_id].keys() & weights[right_id].keys()
                for left_id in representatives(left)
                for right_id in representatives(right)
            )
        )

    def agent_of(ids: set[str]) -> str | None:
        agents = {by_id[rid].agent for rid in ids if by_id[rid].agent}
        return next(iter(agents)) if len(agents) == 1 else None

    def hint_of(ids: set[str]) -> tuple[str, str] | None:
        values = {
            (by_id[rid].stream_hint_source, by_id[rid].stream_hint_digest)
            for rid in ids
            if by_id[rid].stream_hint_source and by_id[rid].stream_hint_digest
        }
        return next(iter(values)) if len(values) == 1 else None

    def affinity(left: set[str], right: set[str]) -> tuple[str, str] | None:
        left_agent, right_agent = agent_of(left), agent_of(right)
        if left_agent and right_agent and left_agent != right_agent:
            return None
        for new_id in reversed(representatives(left)):
            for prior_id in reversed(representatives(right)):
                if _strong_stream_context(weights[new_id], weights[prior_id]):
                    return new_id, prior_id
        return None

    # Prefer the most recently active sustained component for the primary row.
    # The older projection picked its *longest path*, which can strand a newer
    # active exact chain in the catch-all primary group.
    sustained = [
        root for root, ids in components.items() if len(ids) >= 3
    ]
    anchor_root = max(
        sustained or components,
        key=lambda root: max(_request_order(by_id[rid]) for rid in components[root]),
    )
    anchor = components.pop(anchor_root)
    # A confirmed fork or corroborated short cache-affinity split can establish
    # a group before the default three-request rule.
    anchor_confirmed = len(anchor) >= 3 or len(groups) > 1
    confirmed: list[dict[str, Any]] = []
    auxiliary_ids: set[str] = set()
    if anchor_confirmed:
        primary["request_ids"] = ordered_ids(anchor)
        primary["confirmed_request_ids"] = ordered_ids(anchor)
        primary["label"] = "Conversation 1"
        confirmed.append(primary)
    else:
        auxiliary_ids.update(anchor)

    # Existing fork/parallel/cache-affinity evidence has already passed its
    # stronger, specialized tests. Preserve its shared-history membership.
    confirmed.extend(groups[1:])
    reference_groups: list[set[str]] = [set(group["request_ids"]) for group in confirmed]
    for ids in sorted(components.values(), key=lambda values: (
        len(values), max(_request_order(by_id[rid]) for rid in values),
    ), reverse=True):
        joined = False
        for index, reference in enumerate(reference_groups):
            match = affinity(ids, reference)
            if match is None:
                continue
            new_id, prior_id = match
            group = confirmed[index]
            group["request_ids"] = ordered_ids(set(group["request_ids"]) | ids)
            group["confirmed_request_ids"] = ordered_ids(
                set(group["confirmed_request_ids"]) | ids
            )
            reference.update(ids)
            if new_id not in bridges:
                bridges[new_id] = {
                    "prior_request_id": prior_id,
                    "evidence": "context_affinity",
                    "parent_edge": False,
                }
            joined = True
            break
        if joined:
            continue
        candidate_agent = agent_of(ids)
        different_agent = bool(
            candidate_agent and reference_groups
            and all(agent_of(reference) not in (None, candidate_agent)
                    for reference in reference_groups)
        )
        candidate_hint = hint_of(ids)
        distinct_hint = bool(
            candidate_hint and candidate_agent and reference_groups
            and all(
                agent_of(reference) != candidate_agent or
                (hint_of(reference) is not None and hint_of(reference) != candidate_hint)
                for reference in reference_groups
            )
        )
        separate = (
            len(ids) >= 3 and reference_groups
            and all(distinctive(ids, reference) for reference in reference_groups)
            and (different_agent or distinct_hint or len(ids) >= 10)
        )
        if separate:
            anchor_request = by_id[min(ids, key=lambda rid: _request_order(by_id[rid]))]
            group = {
                "key": f"session:{anchor_request.session_id}:agent_stream:{anchor_request.id}",
                "label": f"Conversation {len(confirmed) + 1}",
                "evidence": (
                    "independent_agent_stream" if different_agent else
                    "distinct_stream_hint" if distinct_hint else "sustained_chain"
                ),
                "fork_parent_request_id": None,
                "request_ids": ordered_ids(ids),
                "confirmed_request_ids": ordered_ids(ids),
            }
            confirmed.append(group)
            reference_groups.append(set(ids))
        elif not reference_groups and len(ids) >= 3:
            # A first supported stream need not prove separation from a stream
            # that does not yet exist.
            anchor_request = by_id[min(ids, key=lambda rid: _request_order(by_id[rid]))]
            group = {
                "key": f"session:{anchor_request.session_id}:stream:{anchor_request.id}",
                "label": "Conversation 1",
                "evidence": "default",
                "fork_parent_request_id": None,
                "request_ids": ordered_ids(ids),
                "confirmed_request_ids": ordered_ids(ids),
            }
            confirmed.append(group)
            reference_groups.append(set(ids))
        else:
            auxiliary_ids.update(ids)

    # If there was no established primary but a qualifying later component was
    # promoted, it is the first numbered conversation.
    for number, group in enumerate(confirmed, start=1):
        group["label"] = f"Conversation {number}"
    auxiliary = None
    if auxiliary_ids:
        session_id = internal[0].session_id or "unassigned"
        auxiliary = {
            "key": f"session:{session_id}:auxiliary",
            "label": "Auxiliary requests",
            "evidence": "auxiliary",
            "fork_parent_request_id": None,
            "request_ids": ordered_ids(auxiliary_ids),
            "confirmed_request_ids": [],
        }
    return confirmed, auxiliary


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
                "conversations": [], "auxiliary": None,
                "auxiliary_request_count": 0,
                "membership": {}, "stream_bridges": {}}

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
    affinity_groups, primary_confirmed, bridges = _stream_affinity_groups(
        internal, edges, primary_path, primary_ids, len(groups) + 1,
    )
    if affinity_groups:
        promoted_ids = set().union(*(set(group["request_ids"]) for group in affinity_groups))
        groups[0]["request_ids"] = [rid for rid in groups[0]["request_ids"]
                                    if rid not in promoted_ids]
        groups.extend(affinity_groups)
    groups[0]["confirmed_request_ids"] = sorted(
        primary_confirmed & set(groups[0]["request_ids"]),
        key=lambda rid: _request_order(by_id[rid]),
    )
    groups, auxiliary = _finalize_activity_groups(internal, edges, groups, bridges)
    membership: dict[str, list[dict[str, str]]] = defaultdict(list)
    for group in groups:
        confirmed_ids = set(group["confirmed_request_ids"])
        for rid in group["request_ids"]:
            membership[rid].append({
                "key": group["key"],
                "state": "confirmed" if rid in confirmed_ids else "unassigned",
            })
    if auxiliary:
        for rid in auxiliary["request_ids"]:
            membership[rid].append({"key": auxiliary["key"], "state": "provisional_unassigned"})
    return {
        "lineage_paths": [{"request_ids": path, "leaf_request_id": path[-1]}
                          for path in paths],
        "lineage_fragment_count": len(paths),
        "conversation_count": len(groups),
        "confirmed_parallel_streams": max(0, len(groups) - 1),
        "conversations": groups,
        "auxiliary": auxiliary,
        "auxiliary_request_count": len(auxiliary["request_ids"]) if auxiliary else 0,
        "membership": dict(membership),
        "stream_bridges": bridges,
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
            score = _score_candidate(candidate, child, frequencies, document_count)
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
                    item.request, descendant, frequencies, document_count,
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
    return {
        "parent_request_id": parent.id,
        "child_request_id": child.id,
        "delta": diff_contexts(parent.blocks, child.blocks).to_dict(include_mappings=True),
    }
