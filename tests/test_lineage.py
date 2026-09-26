# Copyright 2026 Rimantas Zukaitis
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
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


def test_many_unlinked_codex_requests_are_one_display_group():
    requests = [_snapshot(f"r{i}", i, []) for i in range(1, 16)]
    graph = build_lineage_graph(requests)
    assert graph["lineage_fragment_count"] == 15
    assert graph["conversation_count"] == 1
    assert graph["confirmed_parallel_streams"] == 0
    assert len(graph["conversations"][0]["request_ids"]) == 15
    assert all(node["conversation_membership"][0]["state"] == "unassigned"
               for node in graph["nodes"])


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
    assert sum("unknown" in group["request_ids"] for group in groups) == 1
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
    assert graph["conversation_count"] == 1
    assert graph["lineage_fragment_count"] >= 2


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
    assert graph["conversation_count"] == 1
    assert graph["lineage_fragment_count"] == 1
    assert child_node["conversation_membership"][0]["state"] == "confirmed"

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
