from __future__ import annotations

from contextspy.analysis.adapters.anthropic import AnthropicAdapter
from contextspy.analysis.capture import CapturedEvent
from contextspy.analysis.invocations import (
    CanonicalInvocation, CanonicalJsonDocument, analyze_invocation,
)
from contextspy.analysis.classifier import classify
from contextspy.normalization import (
    ObservedInvocation,
    PersistedCanonicalInvocation,
    normalize_invocation,
)


def doc(value):
    return CanonicalJsonDocument.from_value(value)


class Lineage:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, provider, response_id):
        return self.values.get((provider, response_id))


def observed(request, response=None, *, text=None):
    return ObservedInvocation(
        provider="anthropic",
        provider_protocol="anthropic",
        protocol_id="anthropic_messages",
        request_payload=request,
        observed_request_text=text,
        response=doc(response) if response is not None else None,
        outcome="completed",
    )


def root():
    request = {
        "thread": {"type": "create"},
        "model": "claude-test",
        "system": [{"type": "text", "text": "stable instructions"}],
        "tools": [{"name": "shell", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "inspect"}],
    }
    response = {
        "id": "msg_root", "role": "assistant",
        "content": [{"type": "tool_use", "id": "tool_1", "name": "shell", "input": {}}],
    }
    return request, response


def test_thread_continuation_expands_exact_parent_and_derives_blocks():
    request, response = root()
    canonical_root = normalize_invocation(observed(request, response), Lineage())
    lineage = Lineage({
        ("anthropic", "msg_root"): PersistedCanonicalInvocation(
            request=canonical_root.request, response=canonical_root.response,
        ),
    })
    child = {
        "thread": {"type": "continue", "previous_message_id": "msg_root"},
        "model": "claude-test",
        "messages": [{"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "tool_1", "content": "ok",
        }]}],
    }

    canonical = normalize_invocation(
        observed(child, {"id": "msg_child", "role": "assistant", "content": []}), lineage,
    )

    assert canonical.predecessor_response_id == "msg_root"
    assert canonical.provider_response_id == "msg_child"
    assert canonical.context_fidelity == "complete"
    assert "thread" not in canonical.request.value
    assert canonical.request.value["system"] == request["system"]
    assert canonical.request.value["tools"] == request["tools"]
    assert canonical.request.value["messages"] == [
        request["messages"][0],
        {"role": "assistant", "content": response["content"]},
        child["messages"][0],
    ]
    blocks = analyze_invocation(canonical, AnthropicAdapter()).analyzed.input_blocks
    assert any(block.tool_name == "shell" for block in blocks)
    assert any(block.tool_call_id == "tool_1" and block.content == "ok" for block in blocks)
    explicit = doc({
        "model": "claude-test",
        "system": request["system"], "tools": request["tools"],
        "messages": canonical.request.value["messages"],
    })
    explicit_analysis = analyze_invocation(
        CanonicalInvocation(request=explicit, response=canonical.response),
        AnthropicAdapter(),
    ).analyzed
    reconstructed_analysis = analyze_invocation(canonical, AnthropicAdapter()).analyzed
    assert [(b.block_type, b.content, b.token_count) for b in reconstructed_analysis.input_blocks] == [
        (b.block_type, b.content, b.token_count) for b in explicit_analysis.input_blocks
    ]
    assert classify(reconstructed_analysis).to_db_fields() == classify(explicit_analysis).to_db_fields()


def test_thread_missing_parent_is_partial_but_keeps_exact_reference():
    child = {
        "thread": {"type": "continue", "previous_message_id": "missing"},
        "messages": [{"role": "user", "content": "visible delta"}],
    }
    canonical = normalize_invocation(observed(child, {"id": "msg_child"}), Lineage())

    assert canonical.predecessor_response_id == "missing"
    assert canonical.context_fidelity == "partial"
    assert canonical.request.value["messages"] == child["messages"]
    assert "thread" not in canonical.request.value


def test_thread_self_reference_is_partial_even_if_a_row_matches():
    request, response = root()
    lineage = Lineage({
        ("anthropic", "msg_self"): PersistedCanonicalInvocation(
            request=doc(request), response=doc({**response, "id": "msg_self"}),
        ),
    })
    canonical = normalize_invocation(observed({
        "thread": {"type": "continue", "previous_message_id": "msg_self"},
        "messages": [{"role": "user", "content": "current"}],
    }, {"id": "msg_self", "role": "assistant", "content": []}), lineage)
    assert canonical.context_fidelity == "partial"
    assert canonical.request.value["messages"] == [{"role": "user", "content": "current"}]


def test_two_forks_follow_only_their_explicit_parent():
    request, response = root()
    lineage = Lineage({
        ("anthropic", "msg_root"): PersistedCanonicalInvocation(
            request=doc(request), response=doc(response),
        ),
    })
    def branch(text):
        return normalize_invocation(observed({
            "thread": {"type": "continue", "previous_message_id": "msg_root"},
            "messages": [{"role": "user", "content": text}],
        }), lineage)

    first = branch("branch one")
    second = branch("branch two")
    assert first.request.value["messages"][-1]["content"] == "branch one"
    assert second.request.value["messages"][-1]["content"] == "branch two"
    assert "branch one" not in str(second.request.value["messages"])


def test_partial_ancestor_stays_partial_after_later_captured_response():
    lineage = Lineage({
        ("anthropic", "msg_partial"): PersistedCanonicalInvocation(
            request=doc({"messages": [{"role": "user", "content": "known tail"}]}),
            response=doc({"id": "msg_partial", "role": "assistant", "content": [
                {"type": "text", "text": "known reply"},
            ]}),
            context_fidelity="partial",
        ),
    })
    canonical = normalize_invocation(observed({
        "thread": {"type": "continue", "previous_message_id": "msg_partial"},
        "messages": [{"role": "user", "content": "new tail"}],
    }), lineage)
    assert canonical.context_fidelity == "partial"
    assert len(canonical.request.value["messages"]) == 3
    assert any("earlier predecessor" in note for note in canonical.context_notes)


def test_cache_diagnostics_id_is_not_a_thread_predecessor():
    request = {
        "diagnostics": {"previous_message_id": "cache_only"},
        "messages": [{"role": "user", "content": "hello"}],
    }
    canonical = normalize_invocation(observed(request), Lineage())
    assert canonical.predecessor_response_id is None
    assert canonical.request.value == request


def test_inherited_system_tail_is_visible_but_fidelity_is_partial():
    root_request, root_response = root()
    root_request["system"].append({"type": "text", "text": "stable tail"})
    lineage = Lineage({
        ("anthropic", "msg_root"): PersistedCanonicalInvocation(
            request=doc(root_request), response=doc(root_response),
        ),
    })
    child = {
        "thread": {"type": "continue", "previous_message_id": "msg_root"},
        "system": [{"type": "text", "text": "new leading instruction"}],
        "messages": [{"role": "user", "content": "next"}],
    }
    canonical = normalize_invocation(observed(child), lineage)
    assert canonical.request.value["system"] == [
        child["system"][0], root_request["system"][1],
    ]
    assert canonical.context_fidelity == "partial"


def test_applied_context_edit_is_opaque_not_silently_rewritten():
    request, _ = root()
    request["context_management"] = {"edits": [{"type": "clear_thinking_20251015"}]}
    response = {
        "id": "msg_root", "role": "assistant", "content": [],
        "context_management": {"applied_edits": [{
            "type": "clear_thinking_20251015", "cleared_input_tokens": 10,
        }]},
    }
    canonical = normalize_invocation(observed(request, response), Lineage())
    assert canonical.context_fidelity == "opaque"
    assert canonical.request.value["messages"] == request["messages"]


def test_redacted_predecessor_content_remains_visible_as_opaque_marker():
    request, response = root()
    response["content"] = [{"type": "redacted_thinking", "data": "opaque"}]
    lineage = Lineage({
        ("anthropic", "msg_root"): PersistedCanonicalInvocation(
            request=doc(request), response=doc(response),
        ),
    })
    child = {
        "thread": {"type": "continue", "previous_message_id": "msg_root"},
        "messages": [{"role": "user", "content": "next"}],
    }
    canonical = normalize_invocation(observed(child), lineage)
    assert canonical.context_fidelity == "opaque"
    assert canonical.request.value["messages"][1]["content"] == response["content"]


def test_sse_reconstruction_retains_applied_context_edits():
    events = [
        CapturedEvent(sequence=0, direction="server_to_client", payload={
            "type": "message_start", "message": {
                "id": "msg_1", "role": "assistant", "content": [],
            },
        }),
        CapturedEvent(sequence=1, direction="server_to_client", payload={
            "type": "message_delta", "delta": {"stop_reason": "end_turn"},
            "context_management": {"applied_edits": [{
                "type": "clear_thinking_20251015", "cleared_input_tokens": 10,
            }]},
        }),
        CapturedEvent(sequence=2, direction="server_to_client", payload={
            "type": "message_stop",
        }),
    ]
    reconstructed = AnthropicAdapter().reconstruct_response(events, transport="sse")
    assert reconstructed.payload["context_management"]["applied_edits"][0]["cleared_input_tokens"] == 10
