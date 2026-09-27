# Copyright 2026 Rimantas Zukaitis
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from contextspy.analysis.blocks import BlockType, Direction
from contextspy.analysis.context_diff import ContextBlock, diff_contexts
from contextspy.analysis.lineage import LineageEdge, RequestSnapshot, _conversation_projection, build_lineage_graph


BASE_TIME = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _block(
    request_id: str,
    block_id: int,
    direction: str,
    block_type: str,
    text: str,
    *,
    position: int,
    category: str | None = "conversation_history",
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> ContextBlock:
    return ContextBlock(
        id=block_id,
        request_id=request_id,
        direction=direction,
        position=position,
        message_index=position,
        block_type=block_type,
        category=category,
        content_hash=_hash(text) if text else None,
        token_count=max(1, len(text.split())),
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


def _snapshot(
    request_id: str,
    sequence: int,
    blocks: list[ContextBlock],
    *,
    response_id: str | None = None,
    predecessor_id: str | None = None,
) -> RequestSnapshot:
    timestamp = BASE_TIME + timedelta(seconds=sequence)
    return RequestSnapshot(
        id=request_id,
        session_id="capture-1",
        session_seq=sequence,
        timestamp=timestamp,
        started_at=timestamp - timedelta(milliseconds=500),
        duration_ms=500,
        provider="openai",
        model="gpt-test",
        agent="codex",
        endpoint="/v1/responses",
        provider_response_id=response_id,
        predecessor_response_id=predecessor_id,
        context_fidelity="complete",
        tokens_total_input=sum(block.token_count for block in blocks if block.direction == Direction.INPUT),
        tokens_total_output=sum(block.token_count for block in blocks if block.direction == Direction.OUTPUT),
        blocks=tuple(blocks),
    )


def test_context_diff_maps_persisted_promoted_added_removed_and_replaced():
    parent = [
        _block("p", 1, Direction.INPUT, BlockType.SYSTEM_PROMPT, "old instructions", position=0, category="system_prompt"),
        _block("p", 2, Direction.INPUT, BlockType.USER_MESSAGE, "first question", position=1),
        _block("p", 3, Direction.INPUT, BlockType.USER_MESSAGE, "removed note", position=2),
        _block("p", 4, Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, "first answer", position=0, category=None),
    ]
    child = [
        _block("c", 5, Direction.INPUT, BlockType.SYSTEM_PROMPT, "new instructions", position=0, category="system_prompt"),
        _block("c", 6, Direction.INPUT, BlockType.USER_MESSAGE, "first question", position=1),
        _block("c", 7, Direction.INPUT, BlockType.ASSISTANT_MESSAGE, "first answer", position=2),
        _block("c", 8, Direction.INPUT, BlockType.USER_MESSAGE, "second question", position=3, category="current_user_message"),
    ]

    delta = diff_contexts(parent, child)

    assert [value.to_dict() for value in delta.persisted] == [
        {"parent_block_id": 2, "child_block_id": 6},
    ]
    assert [value.to_dict() for value in delta.promoted] == [
        {"parent_block_id": 4, "child_block_id": 7},
    ]
    assert delta.added == [8]
    assert delta.removed == [3]
    assert delta.replaced[0].slot == "system_prompt:0"
    assert delta.summary["added"]["blocks"] == 1
    assert delta.summary["promoted"]["blocks"] == 1


def test_context_diff_keeps_unfingerprinted_blocks_out_of_change_claims():
    parent = [
        _block("p", 1, Direction.INPUT, BlockType.USER_MESSAGE, "", position=0),
    ]
    child = [
        _block("c", 2, Direction.INPUT, BlockType.USER_MESSAGE, "", position=0),
    ]

    delta = diff_contexts(parent, child)

    assert delta.persisted == []
    assert delta.added == []
    assert delta.removed == []
    assert delta.replaced == []
    assert delta.unavailable_parent_blocks == [1]
    assert delta.unavailable_child_blocks == [2]


def test_exact_predecessors_form_a_fork_without_using_capture_adjacency():
    root = _snapshot("root", 1, [], response_id="resp-root")
    unrelated = _snapshot("unrelated", 2, [], response_id="resp-other")
    child_b = _snapshot("child-b", 3, [], response_id="resp-b", predecessor_id="resp-root")
    child_a = _snapshot("child-a", 4, [], response_id="resp-a", predecessor_id="resp-root")

    graph = build_lineage_graph([root, unrelated, child_b, child_a])

    exact_edges = {
        (edge["source_request_id"], edge["target_request_id"])
        for edge in graph["edges"] if edge["certainty"] == "exact"
    }
    assert exact_edges == {("root", "child-a"), ("root", "child-b")}
    root_node = next(node for node in graph["nodes"] if node["request_id"] == "root")
    assert root_node["is_fork"] is True
    assert next(node for node in graph["nodes"] if node["request_id"] == "unrelated")["parent_state"] == "unavailable"
    # Two one-off siblings are a diagnostic fork, not yet two conversations.
    assert graph["conversation_count"] == 1
    assert graph["lineage_fragment_count"] == 3


def test_many_unlinked_codex_requests_stay_in_auxiliary_block():
    requests = [_snapshot(f"r{i}", i, []) for i in range(1, 16)]
    graph = build_lineage_graph(requests)
    assert graph["lineage_fragment_count"] == 15
    assert graph["conversation_count"] == 0
    assert graph["confirmed_parallel_streams"] == 0
    assert graph["auxiliary_request_count"] == 15
    assert graph["auxiliary"]["label"] == "Auxiliary requests"
    assert len(graph["auxiliary"]["request_ids"]) == 15
    assert all(node["conversation_membership"][0]["state"] == "provisional_unassigned"
               for node in graph["nodes"])


def _stream_snapshot(request_id: str, sequence: int, hint: str, family: str,
                     predecessor: str | None = None) -> RequestSnapshot:
    blocks = [
        _block(request_id, sequence * 10 + index, Direction.INPUT,
               BlockType.TOOL_RESULT, f"{family}-{index} " * 50,
               position=index, category="tool_results")
        for index in range(3)
    ]
    return replace(
        _snapshot(request_id, sequence, blocks, response_id=request_id,
                  predecessor_id=predecessor),
        stream_hint_source="openai_prompt_cache_key", stream_hint_digest=hint,
    )


def test_stream_affinity_bridges_gaps_without_inventing_parent_edges():
    requests = [
        _stream_snapshot("r293", 293, "B", "beta"),
        _stream_snapshot("r294", 294, "B", "beta", "r293"),
        _stream_snapshot("r402", 402, "A", "alpha"),
        _stream_snapshot("r403", 403, "A", "alpha", "r402"),
        _stream_snapshot("r404", 404, "A", "alpha", "r403"),
        _stream_snapshot("r405", 405, "B", "beta"),
        _stream_snapshot("r406", 406, "B", "beta", "r405"),
        _stream_snapshot("r407", 407, "A", "alpha"),
        _stream_snapshot("r408", 408, "A", "alpha", "r407"),
        _stream_snapshot("r409", 409, "A", "alpha", "r408"),
        _stream_snapshot("r410", 410, "A", "alpha", "r409"),
    ]
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 2
    primary, secondary = graph["conversations"]
    assert {"r404", "r407", "r408", "r409", "r410"} <= set(primary["request_ids"])
    assert {"r294", "r405", "r406"} <= set(secondary["request_ids"])
    assert secondary["evidence"] == "stream_affinity"
    assert graph["stream_bridges"]["r407"]["prior_request_id"] == "r404"
    assert graph["stream_bridges"]["r405"]["prior_request_id"] == "r294"
    assert graph["stream_bridges"]["r407"]["parent_edge"] is False
    assert all(edge["target_request_id"] not in {"r405", "r407"}
               for edge in graph["edges"])
    # Sparse/opaque transport metadata need not erase independently retained
    # block fingerprints, as in the observed Codex capture.
    opaque = [replace(request, context_fidelity="opaque") for request in requests]
    assert build_lineage_graph(opaque)["conversation_count"] == 2


def test_stream_hint_requires_context_and_independent_chain():
    unrelated = [
        _stream_snapshot("a1", 1, "A", "alpha"),
        _stream_snapshot("a2", 2, "A", "alpha", "a1"),
        _stream_snapshot("a3", 3, "A", "alpha", "a2"),
        _stream_snapshot("one-off", 4, "B", "beta"),
    ]
    assert build_lineage_graph(unrelated)["conversation_count"] == 1
    same_hint_unrelated = unrelated[:3] + [
        _stream_snapshot("other1", 4, "A", "beta"),
        _stream_snapshot("other2", 5, "A", "beta", "other1"),
    ]
    assert build_lineage_graph(same_hint_unrelated)["conversation_count"] == 1
    no_context = [replace(item, blocks=(), context_fidelity="opaque") for item in unrelated[:3]] + [
        replace(_stream_snapshot("b1", 4, "B", "beta"), blocks=(), context_fidelity="opaque"),
        replace(_stream_snapshot("b2", 5, "B", "beta", "b1"), blocks=(), context_fidelity="opaque"),
    ]
    assert build_lineage_graph(no_context)["conversation_count"] == 1


def test_exact_chain_overrides_hint_rotation():
    requests = [
        _stream_snapshot("a1", 1, "A", "alpha"),
        _stream_snapshot("a2", 2, "B", "alpha", "a1"),
        _stream_snapshot("a3", 3, "A", "alpha", "a2"),
    ]
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 1
    assert len([edge for edge in graph["edges"] if edge["certainty"] == "exact"]) == 2
    assert "hint_conflict" in next(edge for edge in graph["edges"]
                                   if edge["target_request_id"] == "a2")["evidence"]["reason_codes"]


def test_sustained_fork_shares_only_proven_ancestry():
    requests = [
        _snapshot("root", 1, [], response_id="root"),
        _snapshot("a", 2, [], response_id="a", predecessor_id="root"),
        _snapshot("b", 3, [], response_id="b", predecessor_id="root"),
        _snapshot("aa", 4, [], response_id="aa", predecessor_id="a"),
        _snapshot("bb", 5, [], response_id="bb", predecessor_id="b"),
        _snapshot("unknown", 6, []),
    ]
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 2
    groups = graph["conversations"]
    assert all(group["evidence"] == "fork" for group in groups)
    assert all("root" in group["request_ids"] for group in groups)
    assert all("unknown" not in group["request_ids"] for group in groups)
    assert "unknown" in graph["auxiliary"]["request_ids"]
    assert graph["lineage_fragment_count"] == 3


def test_overlapping_retry_siblings_do_not_become_two_conversations():
    from dataclasses import replace
    root = _snapshot("root", 1, [], response_id="root")
    shared = _block("a", 10, Direction.INPUT, BlockType.USER_MESSAGE, "same prompt", position=0)
    a = _snapshot("a", 2, [shared], response_id="a", predecessor_id="root")
    b = _snapshot("b", 3, [replace(shared, id=11, request_id="b")],
                  response_id="b", predecessor_id="root")
    b = replace(b, started_at=a.started_at + timedelta(milliseconds=100))
    assert build_lineage_graph([root, a, b])["conversation_count"] == 1

    different = _block("b", 11, Direction.INPUT, BlockType.USER_MESSAGE, "other prompt", position=0)
    b = replace(b, blocks=(different,))
    assert build_lineage_graph([root, a, b])["conversation_count"] == 2


def test_external_fork_parent_is_evidence_but_not_a_session_card():
    from dataclasses import replace
    external = replace(_snapshot("outside", 1, [], response_id="outside"),
                       session_id="older", external=True)
    requests = [
        _snapshot("a", 2, [], response_id="a", predecessor_id="outside"),
        _snapshot("b", 3, [], response_id="b", predecessor_id="outside"),
        _snapshot("aa", 4, [], response_id="aa", predecessor_id="a"),
        _snapshot("bb", 5, [], response_id="bb", predecessor_id="b"),
    ]
    graph = build_lineage_graph(requests, external_requests=[external])
    assert graph["conversation_count"] == 2
    assert all(group["fork_parent_request_id"] == "outside" for group in graph["conversations"])
    assert all("outside" not in group["request_ids"] for group in graph["conversations"])


def test_nonoverlapping_inferred_sibling_is_not_a_confirmed_fork():
    requests = [_snapshot(rid, seq, []) for seq, rid in enumerate(
        ("root", "exact-child", "exact-grandchild", "inferred-child", "inferred-grandchild"), 1)]
    edges = [
        LineageEdge("root", "exact-child"),
        LineageEdge("exact-child", "exact-grandchild"),
        LineageEdge("root", "inferred-child", certainty="inferred", confidence=0.82),
        LineageEdge("inferred-child", "inferred-grandchild"),
    ]
    projection = _conversation_projection(requests, edges, {}, [])
    assert projection["lineage_fragment_count"] == 2
    assert projection["conversation_count"] == 1


def test_sustained_interleaved_disjoint_chains_need_observed_and_separate_context():
    def chain(prefix, sequences, context):
        return [
            _snapshot(
                f"{prefix}{i}", sequence,
                [_block(f"{prefix}{i}", sequence, Direction.INPUT,
                        BlockType.USER_MESSAGE, context, position=0)],
                response_id=f"{prefix}{i}",
                predecessor_id=f"{prefix}{i-1}" if i else f"missing-{prefix}",
            )
            for i, sequence in enumerate(sequences)
        ]
    requests = chain("a", [1, 3, 5], "alpha") + chain("b", [2, 4, 6], "beta")
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 2
    assert {group["evidence"] for group in graph["conversations"]} == {"parallel_chains"}

    from dataclasses import replace
    without_observed_starts = [replace(request, started_at=None) for request in requests]
    assert build_lineage_graph(without_observed_starts)["conversation_count"] == 1

    shared_context = chain("a", [1, 3, 5], "same") + chain("b", [2, 4, 6], "same")
    assert build_lineage_graph(shared_context)["conversation_count"] == 1


def test_inference_follows_context_not_interleaved_unrelated_request():
    root_blocks = [
        _block("root", 10, Direction.INPUT, BlockType.SYSTEM_PROMPT, "shared system", position=0, category="system_prompt"),
        _block("root", 11, Direction.INPUT, BlockType.USER_MESSAGE, "inspect alpha", position=1),
        _block("root", 12, Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE, "alpha result", position=0, category=None),
    ]
    unrelated_blocks = [
        _block("other", 20, Direction.INPUT, BlockType.SYSTEM_PROMPT, "shared system", position=0, category="system_prompt"),
        _block("other", 21, Direction.INPUT, BlockType.USER_MESSAGE, "unrelated beta task", position=1),
    ]
    child_blocks = [
        _block("child", 30, Direction.INPUT, BlockType.SYSTEM_PROMPT, "shared system", position=0, category="system_prompt"),
        _block("child", 31, Direction.INPUT, BlockType.USER_MESSAGE, "inspect alpha", position=1),
        _block("child", 32, Direction.INPUT, BlockType.ASSISTANT_MESSAGE, "alpha result", position=2),
        _block("child", 33, Direction.INPUT, BlockType.USER_MESSAGE, "continue alpha", position=3, category="current_user_message"),
    ]

    graph = build_lineage_graph([
        _snapshot("root", 1, root_blocks),
        _snapshot("other", 2, unrelated_blocks),
        _snapshot("child", 3, child_blocks),
    ])

    child_edge = next(edge for edge in graph["edges"] if edge["target_request_id"] == "child")
    assert child_edge["source_request_id"] == "root"
    assert child_edge["certainty"] == "inferred"
    assert not any(edge["target_request_id"] == "other" for edge in graph["edges"])


def test_shared_boilerplate_alone_never_creates_a_parent_edge():
    first = _snapshot("first", 1, [
        _block("first", 1, Direction.INPUT, BlockType.SYSTEM_PROMPT, "same", position=0, category="system_prompt"),
        _block("first", 2, Direction.INPUT, BlockType.USER_MESSAGE, "task one", position=1),
    ])
    second = _snapshot("second", 2, [
        _block("second", 3, Direction.INPUT, BlockType.SYSTEM_PROMPT, "same", position=0, category="system_prompt"),
        _block("second", 4, Direction.INPUT, BlockType.USER_MESSAGE, "task two", position=1),
    ])

    graph = build_lineage_graph([first, second])

    assert graph["edges"] == []
    assert next(node for node in graph["nodes"] if node["request_id"] == "second")["parent_state"] == "root"


def test_near_equal_context_candidates_remain_ambiguous():
    shared_text = "distinct retained conversation block"
    first = _snapshot("first", 1, [
        _block("first", 1, Direction.INPUT, BlockType.USER_MESSAGE, shared_text, position=0),
    ])
    second = _snapshot("second", 2, [
        _block("second", 2, Direction.INPUT, BlockType.USER_MESSAGE, shared_text, position=0),
    ])
    child = _snapshot("child", 3, [
        _block("child", 3, Direction.INPUT, BlockType.USER_MESSAGE, shared_text, position=0),
    ])

    graph = build_lineage_graph([first, second, child])

    assert not any(edge["target_request_id"] == "child" for edge in graph["edges"])
    child_node = next(node for node in graph["nodes"] if node["request_id"] == "child")
    assert child_node["parent_state"] == "ambiguous"
    diagnostic = next(item for item in graph["ambiguous_candidates"] if item["request_id"] == "child")
    assert {candidate["request_id"] for candidate in diagnostic["candidates"]} == {"first", "second"}
    assert graph["conversation_count"] == 0
    assert set(graph["auxiliary"]["request_ids"]) == {"first", "second", "child"}
    assert graph["lineage_fragment_count"] >= 2


def test_a_sole_weak_context_candidate_does_not_become_a_parent():
    parent = _snapshot("parent", 1, [
        _block("parent", 1, Direction.INPUT, BlockType.USER_MESSAGE,
               "shared transcript", position=0),
    ])
    child = _snapshot("child", 2, [
        _block("child", 2, Direction.INPUT, BlockType.USER_MESSAGE,
               "shared transcript", position=0),
        _block("child", 3, Direction.INPUT, BlockType.USER_MESSAGE,
               "new unrelated material", position=1),
    ])
    graph = build_lineage_graph([parent, child])
    assert not graph["edges"]
    assert next(node for node in graph["nodes"] if node["request_id"] == "child")["parent_state"] == "ambiguous"
    assert graph["ambiguous_candidates"][0]["candidates"][0]["confidence"] < 0.80


def test_an_exact_successor_does_not_invent_its_ambiguous_roots_parent():
    first = _snapshot("first", 1, [
        _block("first", 1, Direction.INPUT, BlockType.USER_MESSAGE,
               "same transcript", position=0),
    ])
    rival = _snapshot("rival", 2, [
        _block("rival", 2, Direction.INPUT, BlockType.USER_MESSAGE,
               "same transcript", position=0),
    ])
    root = _snapshot("root", 3, [
        _block("root", 3, Direction.INPUT, BlockType.USER_MESSAGE,
               "same transcript", position=0),
    ], response_id="root-response")
    successors = [
        _snapshot("successor", 4, [], response_id="successor-response",
                  predecessor_id="root-response"),
        _snapshot("later", 5, [], predecessor_id="successor-response"),
    ]
    graph = build_lineage_graph([first, rival, root, *successors])
    assert next(node for node in graph["nodes"] if node["request_id"] == "root")["parent_state"] == "ambiguous"
    assert all(edge["target_request_id"] != "root" for edge in graph["edges"])
    assert [(edge["source_request_id"], edge["target_request_id"]) for edge in graph["edges"]
            if edge["certainty"] == "exact"] == [
        ("root", "successor"), ("successor", "later"),
    ]


def test_provider_change_with_retained_context_does_not_split_a_conversation():
    requests = []
    for prefix, provider, agent, offset in (
        ("a", "openai", "codex", 0),
        ("b", "anthropic", "claude_code", 3),
    ):
        for index in range(3):
            rid = f"{prefix}{index}"
            blocks = [
                _block(rid, offset * 100 + index * 10 + position,
                       Direction.INPUT, BlockType.TOOL_RESULT,
                       f"retained-{position} " * 50, position=position)
                for position in range(3)
            ]
            request = _snapshot(rid, offset + index + 1, blocks,
                                response_id=rid,
                                predecessor_id=f"{prefix}{index - 1}" if index else None)
            requests.append(replace(request, provider=provider, agent=agent))
    graph = build_lineage_graph(requests)
    # Two internally linked runs with copied substantive context are not proven
    # separate merely by changing provider/agent labels.
    assert graph["conversation_count"] == 1
    assert graph["auxiliary_request_count"] == 3
    assert not any(edge["target_request_id"] == "b0" for edge in graph["edges"])


def test_short_opaque_context_does_not_prove_independent_agent_streams():
    requests = []
    for prefix, agent, offset in (("a", "codex", 0), ("b", "claude_code", 3)):
        for index in range(3):
            rid = f"{prefix}{index}"
            request = _snapshot(rid, offset + index + 1, [
                _block(rid, offset * 10 + index + 1, Direction.INPUT,
                       BlockType.USER_MESSAGE, f"{prefix} task", position=0),
            ], response_id=rid, predecessor_id=f"{prefix}{index - 1}" if index else None)
            requests.append(replace(request, agent=agent, context_fidelity="opaque"))
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 1
    assert graph["auxiliary_request_count"] == 3


def test_latest_promoted_turn_resolves_nested_claude_ancestors():
    """The preceding turn wins even though every older context is retained."""
    requests = []
    history: list[tuple[str, str]] = [
        (BlockType.USER_MESSAGE, "initial Claude request"),
        (BlockType.TOOL_RESULT, "initial context " * 40),
        (BlockType.USER_MESSAGE, "repository question"),
    ]
    block_id = 1
    for seq in range(272, 277):
        rid = f"r{seq}"
        blocks = []
        for position, (kind, content) in enumerate(history):
            blocks.append(_block(rid, block_id, Direction.INPUT, kind, content,
                                 position=position))
            block_id += 1
        for position, (kind, content) in enumerate((
            (BlockType.THINKING, f"reasoning-{seq}"),
            (BlockType.TOOL_CALL, f"tool-call-{seq}"),
        )):
            blocks.append(_block(rid, block_id, Direction.OUTPUT, kind, content,
                                 position=position))
            block_id += 1
        requests.append(replace(
            _snapshot(rid, seq, blocks), provider="anthropic",
            agent="claude_code", model="claude-sonnet-5", endpoint="/v1/messages",
        ))
        history.extend([
            (BlockType.THINKING, f"reasoning-{seq}"),
            (BlockType.TOOL_CALL, f"tool-call-{seq}"),
            (BlockType.TOOL_RESULT, f"tool-result-{seq}"),
            (BlockType.USER_MESSAGE, f"follow-up-{seq}"),
        ])

    graph = build_lineage_graph(requests)
    parent = {edge["target_request_id"]: edge for edge in graph["edges"]}
    assert [(parent[f"r{seq}"]["source_request_id"], parent[f"r{seq}"]["certainty"])
            for seq in range(273, 277)] == [
                (f"r{seq - 1}", "inferred") for seq in range(273, 277)
            ]
    assert "latest_turn_promoted" in parent["r274"]["evidence"]["reason_codes"]
    assert graph["conversation_count"] == 1
    assert graph["auxiliary"] is None


def test_older_ancestor_remains_a_possible_fork_when_newer_output_is_absent():
    root = _snapshot("root", 1, [
        _block("root", 1, Direction.INPUT, BlockType.USER_MESSAGE,
               "shared task", position=0),
        _block("root", 2, Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE,
               "root answer", position=0),
    ], response_id="root-response")
    newer = _snapshot("newer", 2, [
        _block("newer", 3, Direction.INPUT, BlockType.USER_MESSAGE,
               "shared task", position=0),
        _block("newer", 4, Direction.INPUT, BlockType.ASSISTANT_MESSAGE,
               "root answer", position=1),
        _block("newer", 5, Direction.OUTPUT, BlockType.ASSISTANT_MESSAGE,
               "newer answer", position=0),
    ], predecessor_id="root-response")
    fork = _snapshot("fork", 3, [
        _block("fork", 6, Direction.INPUT, BlockType.USER_MESSAGE,
               "shared task", position=0),
        _block("fork", 7, Direction.INPUT, BlockType.ASSISTANT_MESSAGE,
               "root answer", position=1),
        _block("fork", 8, Direction.INPUT, BlockType.USER_MESSAGE,
               "alternate continuation", position=2),
    ])
    graph = build_lineage_graph([root, newer, fork])
    fork_edge = next(edge for edge in graph["edges"] if edge["target_request_id"] == "fork")
    assert fork_edge["source_request_id"] == "root"
    assert fork_edge["certainty"] == "inferred"
    assert "ancestor_output_older" not in fork_edge["evidence"]["reason_codes"]


def test_parentless_chain_is_promoted_after_three_requests_and_haiku_stays_auxiliary():
    def rich(rid: str, seq: int, family: str, agent: str, predecessor: str | None):
        blocks = [
            _block(rid, seq * 10 + index, Direction.INPUT, BlockType.TOOL_RESULT,
                   f"{family}-{index} " * 50, position=index)
            for index in range(3)
        ]
        return replace(
            _snapshot(rid, seq, blocks, response_id=rid, predecessor_id=predecessor),
            provider="anthropic" if agent == "claude_code" else "openai",
            agent=agent,
        )

    codex = [rich(f"a{seq}", seq, "codex", "codex",
                  f"a{seq - 1}" if seq > 1 else None) for seq in range(1, 4)]
    haiku = [replace(_snapshot(f"h{seq}", seq, [
        _block(f"h{seq}", seq * 10, Direction.INPUT, BlockType.USER_MESSAGE,
               f"tiny-haiku-{seq}", position=0),
    ]), provider="anthropic", agent="claude_code") for seq in (4, 5)]
    claude = [rich(f"c{seq}", seq, "claude", "claude_code",
                   f"c{seq - 1}" if seq > 6 else None) for seq in range(6, 9)]

    for size in (1, 2):
        graph = build_lineage_graph(codex + haiku + claude[:size])
        assert graph["conversation_count"] == 1
        assert set(graph["auxiliary"]["request_ids"]) == {
            "h4", "h5", *(f"c{seq}" for seq in range(6, 6 + size)),
        }

    graph = build_lineage_graph(codex + haiku + claude)
    assert graph["conversation_count"] == 2
    assert {"c6", "c7", "c8"} == set(next(
        group["request_ids"] for group in graph["conversations"]
        if "c8" in group["request_ids"]
    ))
    assert set(graph["auxiliary"]["request_ids"]) == {"h4", "h5"}
    assert not any(edge["target_request_id"] in {"h4", "h5"} for edge in graph["edges"])


def test_strong_context_rejoins_unlinked_component_without_parent_edge():
    root = _stream_snapshot("root", 1, "A", "alpha")
    chain = [_stream_snapshot("a2", 2, "A", "alpha", "root"),
             _stream_snapshot("a3", 3, "A", "alpha", "a2")]
    one_off = replace(_stream_snapshot("one-off", 4, "A", "beta"),
                      stream_hint_source=None, stream_hint_digest=None)
    before = build_lineage_graph([root, *chain, one_off])
    assert "one-off" in before["auxiliary"]["request_ids"]
    resumed = replace(_stream_snapshot("resumed", 5, "A", "alpha", "one-off"),
                      stream_hint_source=None, stream_hint_digest=None)
    after = build_lineage_graph([root, *chain, one_off, resumed])
    assert after["auxiliary"] is None
    assert {"one-off", "resumed"} <= set(after["conversations"][0]["request_ids"])
    assert not any(edge["target_request_id"] == "one-off" for edge in after["edges"])
    assert after["stream_bridges"]["resumed"]["parent_edge"] is False


def test_context_reset_rejoins_sustained_hinted_stream_without_inventing_parent():
    def request(prefix: str, index: int, seq: int, *, predecessor: str | None):
        rid = f"{prefix}{index}"
        blocks = [
            _block(rid, seq * 100 + position, Direction.INPUT, BlockType.TOOL_RESULT,
                   f"shared-{position} " * 64, position=position)
            for position in range(10)
        ] + [
            _block(rid, seq * 100 + 10 + position, Direction.INPUT,
                   BlockType.TOOL_RESULT, f"{prefix}-context-{position} " * 600,
                   position=10 + position)
            for position in range(2)
        ]
        return replace(
            _snapshot(rid, seq, blocks, response_id=rid, predecessor_id=predecessor),
            stream_hint_source="openai_prompt_cache_key", stream_hint_digest="same-hint",
        )

    requests = [
        request("old", index, index + 1,
                predecessor=f"old{index - 1}" if index else None)
        for index in range(4)
    ] + [
        request("new", index, index + 5,
                predecessor=f"new{index - 1}" if index else None)
        for index in range(4)
    ]
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 1
    assert graph["auxiliary"] is None
    assert set(graph["conversations"][0]["request_ids"]) == {r.id for r in requests}
    assert graph["stream_bridges"]["new0"] == {
        "prior_request_id": "old3", "evidence": "compaction_affinity", "parent_edge": False,
    }
    assert not any(edge["target_request_id"] == "new0" for edge in graph["edges"])
    assert next(node for node in graph["nodes"] if node["request_id"] == "new0")["parent_state"] == "ambiguous"

    changed_hint = [replace(r, stream_hint_digest="another-hint") if r.id.startswith("new")
                    else r for r in requests]
    assert "new0" not in build_lineage_graph(changed_hint)["stream_bridges"]
    changed_agent = [replace(r, agent="claude_code") if r.id.startswith("new")
                     else r for r in requests]
    assert "new0" not in build_lineage_graph(changed_agent)["stream_bridges"]
    late_new = [replace(r, timestamp=r.timestamp + timedelta(minutes=10),
                        started_at=r.started_at + timedelta(minutes=10))
                if r.id.startswith("new") else r for r in requests]
    assert "new0" not in build_lineage_graph(late_new)["stream_bridges"]


def test_long_supported_unlinked_chain_is_a_row_not_auxiliary():
    requests = []
    for prefix, offset in (("old", 0), ("new", 4)):
        for index in range(4):
            rid = f"{prefix}{index}"
            requests.append(_snapshot(
                rid, offset + index + 1,
                [_block(rid, offset * 10 + index + 1, Direction.INPUT,
                        BlockType.USER_MESSAGE, f"{prefix} task", position=0)],
                response_id=rid,
                predecessor_id=f"{prefix}{index - 1}" if index else None,
            ))
    graph = build_lineage_graph(requests)
    assert graph["conversation_count"] == 2
    assert graph["auxiliary"] is None
    assert graph["confirmed_parallel_streams"] == 0
    assert any(group["evidence"] == "unresolved_stream" for group in graph["conversations"])


def test_exact_parent_can_be_returned_as_an_external_capture_stub():
    external = _snapshot("external", 1, [], response_id="resp-before")
    external = RequestSnapshot(**{**external.__dict__, "session_id": "capture-before", "external": True})
    child = _snapshot("child", 2, [], predecessor_id="resp-before")

    graph = build_lineage_graph([child], external_requests=[external])

    assert any(node["request_id"] == "external" and node["external"] for node in graph["nodes"])
    edge = graph["edges"][0]
    assert edge["external_source"] is True
    assert edge["certainty"] == "exact"


def test_capture_lineage_api_uses_persisted_requests_and_blocks(tmp_path):
    from contextspy.analysis.blocks import Block
    from contextspy.api.routers.requests import get_request_context_diff
    from contextspy.api.routers.sessions import get_session_lineage
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db

    init_db(tmp_path / "lineage-api.db")
    started_at = BASE_TIME
    with get_db() as db:
        session = crud.create_session(db, "parallel capture")
        root = crud.create_request(db, {
            "id": "api-root",
            "session_id": session.id,
            "timestamp": started_at + timedelta(seconds=1),
            "started_at": started_at,
            "provider": "openai",
            "model": "gpt-test",
            "agent": "codex",
            "endpoint": "/v1/responses",
            "provider_response_id": "resp-api-root",
        })
        root_input = Block.make(
            Direction.INPUT,
            BlockType.USER_MESSAGE,
            "api question",
            token_count=2,
        )
        root_input.category = "current_user_message"
        root_output = Block.make(
            Direction.OUTPUT,
            BlockType.ASSISTANT_MESSAGE,
            "api answer",
            token_count=2,
        )
        crud.insert_blocks(db, root.id, [root_input, root_output])

        child = crud.create_request(db, {
            "id": "api-child",
            "session_id": session.id,
            "timestamp": started_at + timedelta(seconds=3),
            "started_at": started_at + timedelta(seconds=2),
            "provider": "openai",
            "model": "gpt-test",
            "agent": "codex",
            "endpoint": "/v1/responses",
            "provider_response_id": "resp-api-child",
            "predecessor_response_id": "resp-api-root",
        })
        child_input = [
            Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "api question", token_count=2),
            Block.make(Direction.INPUT, BlockType.ASSISTANT_MESSAGE, "api answer", token_count=2),
            Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "api follow-up", token_count=3),
        ]
        for block in child_input:
            block.category = "conversation_history"
        crud.insert_blocks(db, child.id, child_input)
        session_id = session.id

    graph = get_session_lineage(session_id)
    edge = graph["edges"][0]
    assert edge["source_request_id"] == "api-root"
    assert edge["target_request_id"] == "api-child"
    assert edge["certainty"] == "exact"
    assert edge["delta"]["summary"]["promoted"]["blocks"] == 1
    child_node = next(node for node in graph["nodes"] if node["request_id"] == "api-child")
    assert child_node["started_at_source"] == "observed"
    assert child_node["session_seq"] == 2
    assert graph["conversation_count"] == 0
    assert set(graph["auxiliary"]["request_ids"]) == {"api-root", "api-child"}
    assert graph["lineage_fragment_count"] == 1
    assert child_node["conversation_membership"][0]["state"] == "provisional_unassigned"

    detail = get_request_context_diff("api-child", parent_id="api-root")
    assert detail["delta"]["promoted"][0]["parent_block_id"]
    assert detail["delta"]["added"]


def test_capture_sequence_allocation_is_unique_under_concurrent_writes(tmp_path):
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db

    init_db(tmp_path / "concurrent-sequences.db")
    with get_db() as db:
        capture = crud.create_session(db, "concurrent capture")
        capture_id = capture.id

    def insert_request(index: int) -> int:
        with get_db() as db:
            request = crud.create_request(db, {
                "id": f"concurrent-{index}",
                "session_id": capture_id,
                "timestamp": BASE_TIME + timedelta(milliseconds=index),
                "provider": "openai",
                "endpoint": "/v1/responses",
            })
            return request.session_seq

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(insert_request, range(16)))

    assert sorted(sequences) == list(range(1, 17))
