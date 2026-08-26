# Copyright 2026 Rimantas Zukaitis
# Licensed under the Apache License, Version 2.0
from __future__ import annotations

import json
from datetime import datetime, timezone

from contextspy.analysis.capture import CapturedEvent
from contextspy.analysis.conversation import (
    REGISTRY,
    ContextMutation,
    ConversationAdapter,
    InvocationIdentity,
    LogicalRequestKey,
    get_conversation_adapter,
)
from contextspy.db import crud
from contextspy.db.database import get_db, init_db
from contextspy.proxy.addon import ContextSpyAddon, _WsFlowState
from contextspy.proxy.ws_protocols import CompletedExchange


def _metadata(root_turn: str = "turn_root") -> dict:
    return {
        "thread_id": "thread_1",
        "turn_id": root_turn,
        "root_turn_id": root_turn,
        "agent_name": "codex",
    }


def _exchange(
    *, request: dict, response_id: str, output: list[dict], input_tokens: int,
    output_tokens: int = 20, timestamp: float,
) -> CompletedExchange:
    completed = {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "model": "gpt-5-codex",
            "output": output,
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {
                    "cached_tokens": max(input_tokens - 20, 0),
                    "cache_write_tokens": 5,
                },
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": 8},
            },
        },
    }
    return CompletedExchange(
        request_body=request,
        raw_request_text=json.dumps(request),
        events=[CapturedEvent(sequence=0, payload=completed)],
        request_ts=timestamp,
        first_event_ts=timestamp + 0.1,
        last_event_ts=timestamp + 0.5,
    )


def _state() -> _WsFlowState:
    return _WsFlowState(
        session=None,
        provider="openai_chatgpt",
        agent="codex",
        endpoint="/backend-api/codex/responses",
    )


def test_codex_identity_from_nested_metadata():
    adapter = get_conversation_adapter(
        provider="openai_chatgpt",
        endpoint="/backend-api/codex/responses",
        transport="websocket",
        request_body={},
    )
    assert adapter is not None
    request = {
        "previous_response_id": "resp_1",
        "client_metadata": {
            "x-codex-turn-metadata": json.dumps({
                "thread_id": "thread_nested",
                "turn_id": "turn_child",
                "root_turn_id": "turn_root",
                "agent_name": "worker",
            }),
        },
    }
    identity = adapter.identify(
        provider="openai_chatgpt", agent="codex", request_body=request,
        response_body={"id": "resp_2"},
    )
    assert identity.provider_request_id == "resp_2"
    assert identity.previous_provider_request_id == "resp_1"
    assert identity.provider_conversation_id == "thread_nested"
    assert identity.logical_turn_id == "turn_root"
    assert identity.agent_id == "worker"


def test_codex_invocations_group_and_reconstruct_context(tmp_path):
    init_db(tmp_path / "conversation.db")
    addon = ContextSpyAddon()

    first_request = {
        "type": "response.create",
        "model": "gpt-5-codex",
        "instructions": "You are a coding agent.",
        "input": [{"role": "user", "content": "Update the README"}],
        "client_metadata": _metadata(),
    }
    first_output = [{
        "type": "custom_tool_call",
        "id": "item_patch",
        "call_id": "call_patch",
        "name": "apply_patch",
        "input": "*** Begin Patch",
    }]
    addon._handle_ws_exchange(_state(), _exchange(
        request=first_request,
        response_id="resp_1",
        output=first_output,
        input_tokens=100,
        timestamp=1.0,
    ))

    second_request = {
        "type": "response.create",
        "model": "gpt-5-codex",
        "previous_response_id": "resp_1",
        "input": [{
            "type": "custom_tool_call_output",
            "call_id": "call_patch",
            "output": [{"type": "input_text", "text": "Patch applied"}],
        }],
        "client_metadata": _metadata(),
    }
    second_output = [{
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "README updated."}],
    }]
    addon._handle_ws_exchange(_state(), _exchange(
        request=second_request,
        response_id="resp_2",
        output=second_output,
        input_tokens=120,
        timestamp=2.0,
    ))

    with get_db() as db:
        logical_rows = crud.list_logical_requests(db)
        physical_rows = list(reversed(crud.list_requests(db, sort_dir="asc")))
        # Fetch by invocation order instead of relying on timestamp ordering
        # implementation details in this assertion.
        physical_rows = sorted(physical_rows, key=lambda row: row.invocation_seq or 0)

        assert len(logical_rows) == 1
        logical = logical_rows[0]
        assert logical.invocation_count == 2
        assert logical.peak_context_tokens == 120
        assert logical.final_context_tokens == 120
        assert logical.cumulative_input_tokens == 220
        assert logical.cumulative_cached_tokens == 180
        assert logical.cumulative_cache_write_tokens == 10
        assert logical.cumulative_output_tokens == 40
        assert logical.cumulative_reasoning_tokens == 16

        first, second = physical_rows
        assert first.provider_request_id == "resp_1"
        assert first.lineage_status == "root"
        assert second.previous_provider_request_id == "resp_1"
        assert second.lineage_status == "resolved"
        assert second.logical_request_id == first.logical_request_id
        assert second.observed_input_tokens < second.reconstructed_input_tokens
        assert second.provider_input_tokens == 120
        assert second.unattributed_input_tokens == max(
            120 - second.reconstructed_input_tokens, 0,
        )

        snapshot = crud.get_context_snapshot(db, second.id)
        provenances = {block["provenance"] for block in snapshot}
        assert "inherited_input" in provenances
        assert "inherited_output" in provenances
        assert "observed_current" in provenances
        assert not any(
            block["block_type"] == "system_prompt"
            and block["provenance"] == "inherited_input"
            for block in snapshot
        )
        assert any(block["content"] == "Patch applied" for block in snapshot)

        logical_id = logical.id
        second_id = second.id

    from contextspy.api.routers.requests import (
        get_logical_request as get_logical_request_api,
        get_request_context as get_request_context_api,
    )
    logical_payload = get_logical_request_api(logical_id)
    assert logical_payload["logical_request"]["invocation_count"] == 2
    assert len(logical_payload["invocations"]) == 2
    context_payload = get_request_context_api(second_id)
    assert context_payload["lineage"]["lineage_status"] == "resolved"
    assert context_payload["accounting"]["provider_input_tokens"] == 120
    assert context_payload["blocks"]


def test_rest_request_creates_single_logical_request(tmp_path):
    init_db(tmp_path / "rest_singleton.db")
    addon = ContextSpyAddon()
    request = {"model": "gpt-4o", "input": [{"role": "user", "content": "Hello"}]}
    response = {
        "id": "resp_rest",
        "model": "gpt-4o",
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi"}],
        }],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    adapter = __import__(
        "contextspy.analysis.adapters.openai_responses",
        fromlist=["OpenAIResponsesAdapter"],
    ).OpenAIResponsesAdapter()
    input_blocks, tool_map = adapter.parse_request(request)
    output_blocks, usage = adapter.parse_response(response)
    from contextspy.analysis.blocks import AnalyzedRequest
    addon._save_request(
        provider="openai", agent="openai_sdk", endpoint="/v1/responses",
        req_body=request,
        analyzed=AnalyzedRequest("gpt-4o", input_blocks, output_blocks, usage, tool_map),
        duration_ms=10,
        raw_resp_text=json.dumps(response),
        status_code=200,
        raw_request_body=json.dumps(request),
    )

    with get_db() as db:
        logical = crud.list_logical_requests(db)
        assert len(logical) == 1
        assert logical[0].invocation_count == 1
        assert logical[0].grouping_confidence == "singleton"


def test_v3_migration_is_discovered_and_backfills_retained_row(tmp_path):
    from contextspy.db import migrations

    init_db(tmp_path / "migration.db")
    request = {
        "type": "response.create",
        "model": "gpt-5-codex",
        "input": [{"role": "user", "content": "Hello"}],
        "client_metadata": _metadata(),
    }
    response = {
        "id": "resp_legacy",
        "model": "gpt-5-codex",
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi"}],
        }],
        "usage": {"input_tokens": 20, "output_tokens": 2},
    }
    with get_db() as db:
        crud.create_request(db, {
            "id": "legacy",
            "timestamp": datetime.now(timezone.utc),
            "provider": "openai_chatgpt",
            "agent": "codex",
            "model": "gpt-5-codex",
            "endpoint": "/backend-api/codex/responses",
            "transport": "websocket",
            "response_complete": 0,
            "raw_request_body": json.dumps(request),
            "raw_response_body": json.dumps(response),
            "provider_input_tokens": 20,
            "provider_output_tokens": 2,
        })
        migrations.set_meta(db, "schema_version", "2")
        migrations.set_meta(db, "pending_data_migrations", "[]")

    with get_db() as db:
        assert migrations.check_and_flag_pending_migrations(db) == [3]
        assert migrations.apply_data_migrations(db) == [3]

    with get_db() as db:
        row = crud.get_request(db, "legacy")
        assert row is not None
        assert bool(row.response_complete) is True
        assert row.logical_request_id is not None
        assert row.provider_request_id == "resp_legacy"
        assert crud.get_context_snapshot(db, "legacy")


def test_websocket_without_http_status_is_success_not_error(tmp_path):
    init_db(tmp_path / "status.db")
    with get_db() as db:
        crud.create_request(db, {
            "id": "successful-ws",
            "timestamp": datetime.now(timezone.utc),
            "provider": "openai_chatgpt",
            "endpoint": "/backend-api/codex/responses",
            "transport": "websocket",
            "response_complete": 1,
        })
    with get_db() as db:
        assert [row.id for row in crud.list_requests(db, status_category="success")] == ["successful-ws"]
        assert crud.list_requests(db, status_category="error") == []


def test_out_of_order_predecessor_reconciles_lineage_and_group(tmp_path):
    init_db(tmp_path / "out_of_order.db")
    addon = ContextSpyAddon()

    child_request = {
        "type": "response.create", "model": "gpt-5-codex",
        "previous_response_id": "resp_parent",
        "input": [{"role": "user", "content": "child delta"}],
    }
    addon._handle_ws_exchange(_state(), _exchange(
        request=child_request,
        response_id="resp_child",
        output=[{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "child output"},
        ]}],
        input_tokens=40,
        timestamp=1.0,
    ))
    with get_db() as db:
        child = crud.get_request_by_provider_id(db, "openai_chatgpt", "resp_child")
        assert child is not None and child.lineage_status == "unresolved_predecessor"

    parent_request = {
        "type": "response.create", "model": "gpt-5-codex",
        "input": [{"role": "user", "content": "parent prompt"}],
    }
    addon._handle_ws_exchange(_state(), _exchange(
        request=parent_request,
        response_id="resp_parent",
        output=[{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "parent output"},
        ]}],
        input_tokens=30,
        timestamp=2.0,
    ))

    with get_db() as db:
        child = crud.get_request_by_provider_id(db, "openai_chatgpt", "resp_child")
        parent = crud.get_request_by_provider_id(db, "openai_chatgpt", "resp_parent")
        assert child is not None and parent is not None
        assert child.lineage_status == "resolved"
        assert child.logical_request_id == parent.logical_request_id
        assert child.invocation_seq == 2
        assert len(crud.list_logical_requests(db)) == 1
        assert any(
            block["content"] == "parent output"
            for block in crud.get_context_snapshot(db, child.id)
        )


def test_compaction_resets_inherited_visible_context(tmp_path):
    init_db(tmp_path / "compaction.db")
    addon = ContextSpyAddon()
    root_request = {
        "type": "response.create", "model": "gpt-5-codex",
        "input": [{"role": "user", "content": "large old context"}],
    }
    addon._handle_ws_exchange(_state(), _exchange(
        request=root_request,
        response_id="resp_before_compaction",
        output=[],
        input_tokens=50,
        timestamp=1.0,
    ))
    compacted_request = {
        "type": "response.create", "model": "gpt-5-codex",
        "previous_response_id": "resp_before_compaction",
        "input": [{"type": "compaction", "encrypted_content": "opaque"}],
    }
    addon._handle_ws_exchange(_state(), _exchange(
        request=compacted_request,
        response_id="resp_after_compaction",
        output=[],
        input_tokens=25,
        timestamp=2.0,
    ))

    with get_db() as db:
        row = crud.get_request_by_provider_id(
            db, "openai_chatgpt", "resp_after_compaction",
        )
        assert row is not None
        assert row.lineage_status == "compacted"
        assert row.context_reconstruction_status == "compacted"
        snapshot = crud.get_context_snapshot(db, row.id)
        assert len(snapshot) == 1
        assert snapshot[0]["attrs"]["opaque"] is True
        assert not any(block["content"] == "large old context" for block in snapshot)


def test_forked_continuations_are_marked(tmp_path):
    init_db(tmp_path / "fork.db")
    addon = ContextSpyAddon()
    root = {
        "type": "response.create", "model": "gpt-5-codex",
        "input": [{"role": "user", "content": "root"}],
    }
    addon._handle_ws_exchange(_state(), _exchange(
        request=root, response_id="resp_fork_root", output=[],
        input_tokens=10, timestamp=1.0,
    ))
    for index in (1, 2):
        child = {
            "type": "response.create", "model": "gpt-5-codex",
            "previous_response_id": "resp_fork_root",
            "input": [{"role": "user", "content": f"branch {index}"}],
        }
        addon._handle_ws_exchange(_state(), _exchange(
            request=child, response_id=f"resp_branch_{index}", output=[],
            input_tokens=20, timestamp=1.0 + index,
        ))
    with get_db() as db:
        branches = [
            crud.get_request_by_provider_id(db, "openai_chatgpt", f"resp_branch_{index}")
            for index in (1, 2)
        ]
        assert all(branch is not None and branch.lineage_status == "forked" for branch in branches)


def test_new_provider_conversation_adapter_registers_without_core_changes():
    class ExampleAdapter(ConversationAdapter):
        adapter_id = "example"

        def matches(self, *, provider, endpoint, transport, request_body):
            return provider == "example" and endpoint == "/socket/generate"

        def identify(self, *, provider, agent, request_body, response_body):
            return InvocationIdentity(
                provider_request_id=(response_body or {}).get("requestKey"),
                previous_provider_request_id=request_body.get("parentKey"),
                provider_conversation_id=request_body.get("room"),
                logical_turn_id=request_body.get("turn"),
                agent_id=agent,
                confidence="explicit",
            )

        def logical_request_key(self, *, provider, identity):
            return LogicalRequestKey(
                provider, identity.provider_conversation_id or "unknown",
                identity.logical_turn_id or "unknown", identity.agent_id,
            )

        def context_mutation(self, *, request_body, identity):
            return ContextMutation(
                inherit_previous=bool(identity.previous_provider_request_id),
                include_previous_output=True,
            )

    adapter = ExampleAdapter()
    REGISTRY.insert(0, adapter)
    try:
        selected = get_conversation_adapter(
            provider="example", endpoint="/socket/generate",
            transport="websocket", request_body={},
        )
        assert selected is adapter
        identity = selected.identify(
            provider="example", agent="example_agent",
            request_body={"room": "r1", "turn": "t1", "parentKey": "p1"},
            response_body={"requestKey": "p2"},
        )
        assert identity.provider_request_id == "p2"
        assert identity.previous_provider_request_id == "p1"
    finally:
        REGISTRY.remove(adapter)
