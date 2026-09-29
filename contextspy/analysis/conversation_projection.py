"""Display-only conversation projection from an accepted request-lineage graph.

A stream bridge never creates a direct parent edge.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping

from contextspy.analysis.context_diff import semantic_key
from contextspy.analysis.lineage_types import (
    LineageEdge, RequestSnapshot, input_blocks as _input_blocks,
    request_order as _request_order,
)

MAX_AFFINITY_CANDIDATES = 32
MAX_AFFINITY_POSTINGS = 64


def _meaningful_input_weights(internal: list[RequestSnapshot]) -> dict[str, dict[tuple, int]]:
    """Prepare the same non-configuration evidence for both grouping phases."""
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
    return weights


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


def _compacted_stream_context(
    left: Mapping[tuple, int], right: Mapping[tuple, int],
) -> bool:
    """Strong fingerprint containment despite a context-size/weight reset.

    This is only used with a matching stream hint, same known agent, and a
    non-overlapping adjacent stream boundary. It never establishes a parent.
    """
    if not left or not right:
        return False
    shared = left.keys() & right.keys()
    if len(shared) < 10 or len(shared) / min(len(left), len(right)) < 0.70:
        return False
    return sum(min(left[key], right[key]) for key in shared) >= 512


def _stream_affinity_groups(
    internal: list[RequestSnapshot], edges: list[LineageEdge],
    primary_path: list[str], primary_ids: set[str], next_number: int,
    weights: dict[str, dict[tuple, int]],
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
        if _strong_stream_context(weights[anchor_latest], weights[latest]):
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
    weights: dict[str, dict[tuple, int]],
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

    def affinity(left: set[str], right: set[str]) -> tuple[str, str, str] | None:
        left_agent, right_agent = agent_of(left), agent_of(right)
        if left_agent and right_agent and left_agent != right_agent:
            return None
        for new_id in reversed(representatives(left)):
            for prior_id in reversed(representatives(right)):
                if _strong_stream_context(weights[new_id], weights[prior_id]):
                    return new_id, prior_id, "context_affinity"

        # A compacted request can retain many distinctive old fingerprints
        # while replacing most token weight. Rejoin only sequential components
        # of the same hinted agent, with a sustained older chain and a nearby
        # boundary; this remains display-only affinity, not a direct edge.
        if not (left_agent and left_agent == right_agent and
                hint_of(left) is not None and hint_of(left) == hint_of(right)):
            return None
        older, newer = sorted((left, right), key=lambda ids: max(
            _request_order(by_id[rid]) for rid in ids
        ))
        older_latest = max(older, key=lambda rid: _request_order(by_id[rid]))
        newer_first = min(newer, key=lambda rid: _request_order(by_id[rid]))
        gap = by_id[newer_first].effective_started_at - by_id[older_latest].timestamp
        if len(older) < 3 or not timedelta(0) <= gap <= timedelta(minutes=5):
            return None
        if any(
            _compacted_stream_context(weights[older_id], weights[newer_id])
            for older_id in representatives(older)
            for newer_id in representatives(newer)
        ):
            return newer_first, older_latest, "compaction_affinity"
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
            new_id, prior_id, bridge_evidence = match
            group = confirmed[index]
            group["request_ids"] = ordered_ids(set(group["request_ids"]) | ids)
            group["confirmed_request_ids"] = ordered_ids(
                set(group["confirmed_request_ids"]) | ids
            )
            reference.update(ids)
            if new_id not in bridges:
                bridges[new_id] = {
                    "prior_request_id": prior_id,
                    "evidence": bridge_evidence,
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
        elif len(ids) >= 4 or (not reference_groups and len(ids) >= 3):
            # A first supported stream need not prove separation from a stream
            # that does not yet exist. A longer coherent chain is a supported
            # stream even if its relationship to other rows is still unknown;
            # it should not languish among one-off auxiliary requests.
            anchor_request = by_id[min(ids, key=lambda rid: _request_order(by_id[rid]))]
            group = {
                "key": f"session:{anchor_request.session_id}:stream:{anchor_request.id}",
                "label": "Conversation 1",
                "evidence": "unresolved_stream" if reference_groups else "default",
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
                "diagnostic_path_count": 0,
                "conversation_count": 0, "confirmed_parallel_streams": 0,
                "conversations": [], "auxiliary": None,
                "auxiliary_request_count": 0,
                "membership": {}, "stream_bridges": {}}

    by_id = {request.id: request for request in internal}
    weights = _meaningful_input_weights(internal)
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
        internal, edges, primary_path, primary_ids, len(groups) + 1, weights,
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
    groups, auxiliary = _finalize_activity_groups(internal, edges, groups, bridges, weights)
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
        "diagnostic_path_count": len(paths),
        "conversation_count": len(groups),
        "confirmed_parallel_streams": sum(
            group["evidence"] != "unresolved_stream" for group in groups[1:]
        ),
        "conversations": groups,
        "auxiliary": auxiliary,
        "auxiliary_request_count": len(auxiliary["request_ids"]) if auxiliary else 0,
        "membership": dict(membership),
        "stream_bridges": bridges,
    }
