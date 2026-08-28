from __future__ import annotations

import json

import pytest

from contextspy.analysis.adapters.openai_responses import OpenAIResponsesAdapter
from contextspy.analysis.capture import CapturedEvent
from contextspy.analysis.invocations import (
    CanonicalInvocation,
    CanonicalJsonDocument,
    analyze_invocation,
)
from contextspy.normalization import (
    ObservedInvocation,
    PersistedCanonicalInvocation,
    normalize_invocation,
)


class MemoryLineage:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, provider: str, response_id: str):
        return self.values.get((provider, response_id))


def document(value: dict) -> CanonicalJsonDocument:
    return CanonicalJsonDocument.from_value(value)


def observed(
    request: dict,
    response: dict | None = None,
    *,
    request_text: str | None = None,
    events: tuple[CapturedEvent, ...] = (),
) -> ObservedInvocation:
    return ObservedInvocation(
        provider="openai_chatgpt",
        provider_protocol="openai_responses",
        protocol_id="codex_responses",
        request_payload=request,
        observed_request_text=request_text,
        response=document(response) if response is not None else None,
        events=events,
        outcome="completed",
    )


def test_canonical_document_retains_exact_text():
    text = '{ "model": "gpt-test", "input": [] }'
    canonical = CanonicalJsonDocument.from_text(text)

    assert canonical.text == text
    assert canonical.value == {"model": "gpt-test", "input": []}


def test_analysis_uses_only_canonical_documents_and_keeps_failures_independent():
    class RequestFailingAdapter(OpenAIResponsesAdapter):
        def parse_request(self, req_body):
            raise ValueError("request failed")

    canonical = CanonicalInvocation(
        request=document({"model": "gpt-test", "input": []}),
        response=document({
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "still parsed"}],
            }],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }),
    )

    result = analyze_invocation(canonical, RequestFailingAdapter())

    assert [issue.stage for issue in result.issues] == ["request_analysis"]
    assert result.analyzed.response_text == "still parsed"
    assert result.analyzed.usage.output_tokens == 2


def test_root_websocket_request_becomes_standalone_provider_json():
    request = {
        "type": "response.create",
        "model": "gpt-test",
        "input": [{"role": "user", "content": "hello"}],
    }
    response = {"id": "resp_1", "model": "gpt-test", "output": [], "usage": {}}

    canonical = normalize_invocation(observed(request, response), MemoryLineage())

    assert canonical.provider_response_id == "resp_1"
    assert canonical.predecessor_response_id is None
    assert canonical.context_fidelity == "complete"
    assert "type" not in canonical.request.value
    assert canonical.request.value["input"] == request["input"]


def test_continuation_expands_predecessor_input_output_and_current_tool_result():
    root_request = document({
        "model": "gpt-test",
        "input": [{"role": "user", "content": "inspect the project"}],
    })
    root_response = document({
        "id": "resp_1",
        "output": [{
            "type": "custom_tool_call", "call_id": "call_1",
            "name": "shell", "input": "pwd",
        }],
        "usage": {"input_tokens": 20, "output_tokens": 4},
    })
    lineage = MemoryLineage({
        ("openai_chatgpt", "resp_1"): PersistedCanonicalInvocation(
            request=root_request, response=root_response,
        )
    })
    current = {
        "type": "response.create",
        "previous_response_id": "resp_1",
        "model": "gpt-test-next",
        "input": [{
            "type": "custom_tool_call_output", "call_id": "call_1",
            "output": "/project",
        }],
    }
    response = {"id": "resp_2", "output": [], "usage": {"input_tokens": 40}}

    canonical = normalize_invocation(observed(current, response), lineage)

    assert canonical.predecessor_response_id == "resp_1"
    assert canonical.provider_response_id == "resp_2"
    assert canonical.context_fidelity == "complete"
    assert canonical.request.value["model"] == "gpt-test-next"
    assert "previous_response_id" not in canonical.request.value
    assert [item.get("type", "message") for item in canonical.request.value["input"]] == [
        "message", "custom_tool_call", "custom_tool_call_output",
    ]

    analysis = analyze_invocation(canonical, OpenAIResponsesAdapter()).analyzed
    assert any(block.content == "inspect the project" for block in analysis.input_blocks)
    assert any(block.tool_name == "shell" for block in analysis.input_blocks)
    assert any(block.content == "/project" for block in analysis.input_blocks)


def test_continuation_never_inherits_predecessor_top_level_configuration():
    lineage = MemoryLineage({
        ("openai_chatgpt", "resp_1"): PersistedCanonicalInvocation(
            request=document({
                "model": "old-model",
                "instructions": "old instructions",
                "tools": [{"type": "function", "name": "old_tool"}],
                "input": [{"role": "user", "content": "hello"}],
            }),
            response=document({"id": "resp_1", "output": []}),
        )
    })
    current = {
        "type": "response.create",
        "previous_response_id": "resp_1",
        "model": "new-model",
        "instructions": "new instructions",
        "input": [{"role": "user", "content": "continue"}],
    }

    canonical = normalize_invocation(observed(current, {"id": "resp_2"}), lineage)

    assert canonical.request.value["model"] == "new-model"
    assert canonical.request.value["instructions"] == "new instructions"
    assert "tools" not in canonical.request.value


def test_missing_predecessor_is_partial_and_never_inferred():
    current = {
        "type": "response.create",
        "previous_response_id": "missing",
        "input": [{"type": "custom_tool_call_output", "output": "only visible item"}],
    }

    canonical = normalize_invocation(observed(current, {"id": "resp_2"}), MemoryLineage())

    assert canonical.context_fidelity == "partial"
    assert canonical.predecessor_response_id == "missing"
    assert canonical.request.value["input"] == current["input"]


def test_provider_managed_conversation_is_explicitly_partial():
    request_text = '{"model":"gpt-5","conversation":"conv_123","input":"next"}'
    current = {"model": "gpt-5", "conversation": "conv_123", "input": "next"}
    invocation = ObservedInvocation(
        provider="openai_chatgpt",
        provider_protocol="openai_responses",
        protocol_id="responses",
        request_payload=current,
        observed_request_text=request_text,
        response=document({"id": "resp_2", "output": []}),
    )

    canonical = normalize_invocation(invocation, MemoryLineage())

    assert canonical.context_fidelity == "partial"
    assert canonical.request.text == request_text
    assert canonical.request.value["conversation"] == "conv_123"
    assert "Provider-managed conversation history" in canonical.context_notes[0]


def test_null_responses_input_does_not_create_an_empty_other_block():
    blocks, _ = OpenAIResponsesAdapter().parse_request({"input": None})

    assert blocks == []


def test_compacted_or_encrypted_state_is_opaque():
    request = {
        "type": "response.create",
        "input": [
            {"type": "compaction", "encrypted_content": "opaque-state"},
            {"role": "user", "content": "continue"},
        ],
    }

    canonical = normalize_invocation(observed(request, {"id": "resp_1"}), MemoryLineage())

    assert canonical.context_fidelity == "opaque"
    analysis = analyze_invocation(canonical, OpenAIResponsesAdapter()).analyzed
    opaque = [block for block in analysis.input_blocks if block.attrs.get("opaque")]
    assert len(opaque) == 1


def test_predecessor_compaction_resets_visible_history():
    compaction = {
        "type": "compaction",
        "encrypted_content": "opaque-state",
    }
    lineage = MemoryLineage({
        ("openai_chatgpt", "resp_1"): PersistedCanonicalInvocation(
            request=document({"input": [{"role": "user", "content": "old history"}]}),
            response=document({"id": "resp_1", "output": [compaction]}),
        )
    })
    current = {
        "type": "response.create",
        "previous_response_id": "resp_1",
        "input": [{"role": "user", "content": "after reset"}],
    }

    canonical = normalize_invocation(observed(current, {"id": "resp_2"}), lineage)

    assert canonical.context_fidelity == "opaque"
    assert canonical.request.value["input"] == [
        compaction,
        {"role": "user", "content": "after reset"},
    ]


def test_response_inject_is_added_to_the_active_invocation():
    request = {"type": "response.create", "input": "initial"}
    inject = CapturedEvent(
        sequence=0,
        direction="client_to_server",
        payload={"type": "response.inject", "input": [{"role": "user", "content": "extra"}]},
    )

    canonical = normalize_invocation(
        observed(request, {"id": "resp_1"}, events=(inject,)), MemoryLineage(),
    )

    assert [item["content"] for item in canonical.request.value["input"]] == [
        "initial", "extra",
    ]


def test_exact_rest_json_is_retained_when_no_state_expansion_is_needed():
    text = '{"model":"gpt-test", "input":[{"role":"user","content":"hi"}]}'
    request = json.loads(text)

    canonical = normalize_invocation(
        observed(request, {"id": "resp_1"}, request_text=text), MemoryLineage(),
    )

    assert canonical.request.text == text


def test_responses_adapter_handles_developer_and_custom_tool_items():
    adapter = OpenAIResponsesAdapter()
    request = {
        "additional_tools": [{"type": "custom", "name": "shell"}],
        "input": [
            {"role": "developer", "content": "be precise"},
            {"type": "custom_tool_call", "call_id": "c1", "name": "shell", "input": "pwd"},
            {"type": "custom_tool_call_output", "call_id": "c1", "output": "/project"},
        ],
    }

    blocks, call_map = adapter.parse_request(request)

    assert call_map == {"c1": "shell"}
    assert any(block.block_type == "system_prompt" for block in blocks)
    assert any(block.block_type == "tool_definition" and block.tool_name == "shell" for block in blocks)
    assert any(block.block_type == "tool_result" and block.tool_name == "shell" for block in blocks)


def test_responses_usage_includes_cache_and_reasoning_breakdowns():
    _, usage = OpenAIResponsesAdapter().parse_response({
        "output": [],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 10},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 20},
        },
    })

    assert usage.input_tokens == 100
    assert usage.cache_read_tokens == 80
    assert usage.cache_creation_tokens == 10
    assert usage.reasoning_tokens == 20


def test_empty_terminal_output_does_not_erase_completed_stream_item():
    adapter = OpenAIResponsesAdapter()
    events = [
        CapturedEvent(sequence=0, payload={
            "type": "response.created",
            "response": {"id": "resp_1", "model": "gpt-test", "output": []},
        }),
        CapturedEvent(sequence=1, payload={
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "checked it"}],
            },
        }),
        CapturedEvent(sequence=2, payload={
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {"type": "custom_tool_call", "call_id": "c1", "name": "shell", "input": "pwd"},
        }),
        CapturedEvent(sequence=3, payload={
            "type": "response.completed",
            "response": {
                "id": "resp_1", "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }),
    ]

    canonical = adapter.reconstruct_response(events, transport="websocket")
    blocks, usage = adapter.parse_response(canonical.payload)

    assert canonical.payload["id"] == "resp_1"
    assert [item["type"] for item in canonical.payload["output"]] == [
        "reasoning", "custom_tool_call",
    ]
    assert any(block.block_type == "thinking" and block.content == "checked it" for block in blocks)
    assert any(block.block_type == "tool_call" and block.tool_name == "shell" for block in blocks)
    assert usage.input_tokens == 10


def test_custom_tool_call_deltas_reconstruct_canonical_input():
    adapter = OpenAIResponsesAdapter()
    events = [
        CapturedEvent(sequence=0, payload={
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "custom_tool_call", "call_id": "c1", "name": "shell"},
        }),
        CapturedEvent(sequence=1, payload={
            "type": "response.custom_tool_call_input.delta", "output_index": 0,
            "delta": "pw",
        }),
        CapturedEvent(sequence=2, payload={
            "type": "response.custom_tool_call_input.done", "output_index": 0,
            "input": "pwd", "call_id": "c1", "name": "shell",
        }),
        CapturedEvent(sequence=3, payload={
            "type": "response.completed",
            "response": {"id": "resp_1", "output": [], "usage": {}},
        }),
    ]

    canonical = adapter.reconstruct_response(events, transport="websocket")

    assert canonical.payload["output"][0]["input"] == "pwd"


def test_codex_websocket_tool_loop_persists_one_full_canonical_row_per_invocation(tmp_path):
    pytest.importorskip("mitmproxy")
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db
    from contextspy.proxy.addon import ContextSpyAddon, _WsFlowState
    from contextspy.proxy.ws_protocols import CompletedExchange

    init_db(tmp_path / "codex_loop.db")
    addon = ContextSpyAddon()
    state = _WsFlowState(
        session=None,
        provider="openai_chatgpt",
        agent="codex",
        endpoint="/backend-api/codex/responses",
        protocol_id="codex_responses",
    )
    tools = [{"type": "custom", "name": "shell", "description": "Run a command"}]

    root_request = {
        "type": "response.create",
        "model": "gpt-test",
        "tools": tools,
        "input": [{"role": "user", "content": "show the directory"}],
    }
    root_events = [
        {"type": "response.created", "response": {
            "id": "resp_1", "model": "gpt-test", "output": [],
        }},
        {"type": "response.output_item.done", "output_index": 0, "item": {
            "type": "custom_tool_call", "call_id": "call_1",
            "name": "shell", "input": "pwd",
        }},
        {"type": "response.completed", "response": {
            "id": "resp_1", "model": "gpt-test", "output": [],
            "usage": {
                "input_tokens": 30,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 5,
            },
        }},
    ]
    addon._handle_ws_exchange(state, CompletedExchange(
        request_body=root_request,
        raw_request_text=json.dumps(root_request),
        events=[CapturedEvent(sequence=i, payload=value) for i, value in enumerate(root_events)],
        request_ts=1.0,
        first_event_ts=1.1,
        last_event_ts=1.2,
        outcome="completed",
    ))

    continuation_request = {
        "type": "response.create",
        "previous_response_id": "resp_1",
        "model": "gpt-test",
        "tools": tools,
        "input": [{
            "type": "custom_tool_call_output", "call_id": "call_1",
            "output": "/project",
        }],
    }
    continuation_events = [
        {"type": "response.created", "response": {
            "id": "resp_2", "model": "gpt-test", "output": [],
        }},
        {"type": "response.output_item.done", "output_index": 0, "item": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "The directory is /project."}],
        }},
        {"type": "response.completed", "response": {
            "id": "resp_2", "model": "gpt-test", "output": [],
            "usage": {
                "input_tokens": 60,
                "input_tokens_details": {"cached_tokens": 45},
                "output_tokens": 8,
            },
        }},
    ]
    addon._handle_ws_exchange(state, CompletedExchange(
        request_body=continuation_request,
        raw_request_text=json.dumps(continuation_request),
        events=[
            CapturedEvent(sequence=i, payload=value)
            for i, value in enumerate(continuation_events)
        ],
        request_ts=2.0,
        first_event_ts=2.1,
        last_event_ts=2.3,
        outcome="completed",
    ))

    with get_db() as db:
        rows = crud.list_requests(db, limit=10)
        assert len(rows) == 2
        root = crud.get_request_by_provider_response_id(db, "openai_chatgpt", "resp_1")
        continuation = crud.get_request_by_provider_response_id(
            db, "openai_chatgpt", "resp_2",
        )
        assert root is not None and continuation is not None
        root_response_body = root.canonical_response_body
        continuation_request_body = continuation.canonical_request_body
        continuation_predecessor = continuation.predecessor_response_id
        continuation_fidelity = continuation.context_fidelity
        continuation_provider_input = continuation.provider_input_tokens
        continuation_cache_read = continuation.cache_read_tokens
        continuation_detail = continuation.to_dict(include_raw=True)
        continuation_blocks = crud.get_blocks(db, continuation.id)

    assert json.loads(root_response_body)["output"][0]["type"] == "custom_tool_call"
    canonical_request = json.loads(continuation_request_body)
    assert [item.get("type", "message") for item in canonical_request["input"]] == [
        "message", "custom_tool_call", "custom_tool_call_output",
    ]
    assert continuation_predecessor == "resp_1"
    assert continuation_fidelity == "complete"
    assert continuation_provider_input == 60
    assert continuation_cache_read == 45
    assert continuation_detail["request_body"] == continuation_request_body
    assert any(block["block_type"] == "tool_call" for block in continuation_blocks)
    assert any(
        block["block_type"] == "tool_result" and block["content"] == "/project"
        for block in continuation_blocks
    )


def test_provider_response_id_is_idempotent(tmp_path):
    pytest.importorskip("mitmproxy")
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db
    from contextspy.proxy.addon import ContextSpyAddon, _WsFlowState
    from contextspy.proxy.ws_protocols import CompletedExchange

    init_db(tmp_path / "codex_duplicate.db")
    addon = ContextSpyAddon()
    state = _WsFlowState(
        session=None, provider="openai_chatgpt", agent="codex",
        endpoint="/backend-api/codex/responses", protocol_id="codex_responses",
    )
    request = {"type": "response.create", "model": "gpt-test", "input": "hello"}
    events = [
        CapturedEvent(sequence=0, payload={
            "type": "response.created", "response": {"id": "resp_same", "output": []},
        }),
        CapturedEvent(sequence=1, payload={
            "type": "response.completed",
            "response": {"id": "resp_same", "output": [], "usage": {}},
        }),
    ]
    exchange = CompletedExchange(
        request_body=request, raw_request_text=json.dumps(request),
        events=events, outcome="completed",
    )

    addon._handle_ws_exchange(state, exchange)
    addon._handle_ws_exchange(state, exchange)

    with get_db() as db:
        assert len(crud.list_requests(db)) == 1


def test_rest_capture_stores_the_exact_canonical_json_given_to_analysis(tmp_path):
    pytest.importorskip("mitmproxy")
    from types import SimpleNamespace

    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db
    from contextspy.proxy.addon import ContextSpyAddon

    init_db(tmp_path / "rest_canonical.db")
    request_text = '{ "model": "gpt-test", "input": [{"role":"user","content":"hello"}] }'
    response_text = json.dumps({
        "id": "resp_rest",
        "model": "gpt-test",
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "hi"}],
        }],
        "usage": {"input_tokens": 8, "output_tokens": 2},
    })
    flow = SimpleNamespace(
        id="flow-rest",
        request=SimpleNamespace(
            pretty_host="api.openai.com",
            port=443,
            path="/v1/responses",
            headers={"user-agent": "openai-python"},
            get_text=lambda: request_text,
        ),
        response=SimpleNamespace(
            status_code=200,
            headers={"content-type": "application/json"},
            get_text=lambda: response_text,
        ),
        websocket=None,
        metadata={"contextspy_request_body": request_text},
    )

    ContextSpyAddon()._handle_response(flow)

    with get_db() as db:
        rows = crud.list_requests(db)
        assert len(rows) == 1
        detail = rows[0].to_dict(include_raw=True)
    assert detail["canonical_request_body"] == request_text
    assert detail["canonical_response_body"] == response_text
    assert detail["request_body"] == request_text
    assert detail["response_body"] == response_text
    assert detail["transport"] == "http"
    assert detail["invocation_outcome"] == "completed"
