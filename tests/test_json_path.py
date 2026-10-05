"""Every adapter records where in the canonical JSON each block came from (Block.json_path)."""
import json

import pytest

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import Block


def resolve(document, path):
    node = document
    for segment in path:
        node = node[segment]
    return node


def _paths(blocks):
    return [(b.block_type, b.json_path) for b in blocks]


def _assert_resolves(document, blocks, *, unlocated=()):
    """Each located block's path must exist and the node must hold the block's text."""
    for block in blocks:
        if block.json_path is None:
            assert block.block_type in unlocated, f"unexpected missing path for {block.block_type}"
            continue
        node = resolve(document, block.json_path)
        # Media blocks hold a short marker such as "[image]" instead of the encoded payload.
        if block.content and not block.attrs.get("contains_media"):
            haystack = node if isinstance(node, str) else json.dumps(node, ensure_ascii=False)
            # A container path (several parts joined into one block) holds the text line by line;
            # JSON encoding escapes quotes/newlines, so accept the escaped form too.
            for line in filter(None, block.content.split("\n")):
                assert line in haystack or json.dumps(line)[1:-1] in haystack, (
                    block.block_type, block.json_path
                )


ANTHROPIC_REQUEST = {
    "model": "claude",
    "system": [{"type": "text", "text": "You are helpful."}, {"type": "text", "text": "Be brief."}],
    "tools": [{"name": "Read", "input_schema": {}}, {"name": "Bash", "input_schema": {}}],
    "messages": [
        {"role": "user", "content": "plain question"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "sig"},
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "a.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file body"},
            {"type": "text", "text": "and continue"},
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
        ]},
    ],
}


def test_anthropic_request_paths():
    blocks, _ = get_adapter("/v1/messages").parse_request(ANTHROPIC_REQUEST)
    assert _paths(blocks) == [
        ("system_prompt", ("system",)),
        ("tool_definition", ("tools", 0)),
        ("tool_definition", ("tools", 1)),
        ("user_message", ("messages", 0, "content")),
        ("thinking", ("messages", 1, "content", 0)),
        ("assistant_message", ("messages", 1, "content", 1)),
        ("tool_call", ("messages", 1, "content", 2)),
        ("tool_result", ("messages", 2, "content", 0)),
        ("user_message", ("messages", 2, "content", 1)),
        ("other", ("messages", 2, "content", 2)),
    ]
    _assert_resolves(ANTHROPIC_REQUEST, blocks, unlocated=())


def test_anthropic_string_system_and_response_paths():
    blocks, _ = get_adapter("/v1/messages").parse_request({"system": "plain", "messages": []})
    assert _paths(blocks) == [("system_prompt", ("system",))]

    response = {"content": [
        {"type": "thinking", "thinking": "plan"},
        {"type": "text", "text": "answer"},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
    ], "usage": {"input_tokens": 1, "output_tokens": 9}}
    out, _ = get_adapter("/v1/messages").parse_response(response)
    assert _paths(out) == [
        ("thinking", ("content", 0)),
        ("assistant_message", ("content", 1)),
        ("tool_call", ("content", 2)),
    ]
    _assert_resolves(response, out)

    string_response = {"content": "just text", "usage": {}}
    out, _ = get_adapter("/v1/messages").parse_response(string_response)
    assert _paths(out) == [("assistant_message", ("content",))]


def test_anthropic_reconciled_thinking_block_has_no_location():
    # Billed reasoning that never appears in the output is synthesised, so nothing to point at.
    out, _ = get_adapter("/v1/messages").parse_response({
        "content": [{"type": "text", "text": "hi"}], "usage": {},
    })
    assert all(b.json_path is not None for b in out)
    from contextspy.analysis.adapters.base import reconcile_thinking
    from contextspy.analysis.blocks import Usage
    blocks = list(out)
    reconcile_thinking(blocks, Usage(reasoning_tokens=50))
    synthetic = [b for b in blocks if b.block_type == "thinking"]
    assert len(synthetic) == 1 and synthetic[0].json_path is None


CHAT_REQUEST = {
    "model": "m",
    "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "part one"}, {"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "user", "content": "string user"},
        {"role": "assistant", "content": "calling", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"p\":1}"}},
            {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"p\":2}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result one"},
        {"role": "tool", "tool_call_id": "c2", "content": "result two"},
    ],
}


def test_openai_chat_request_paths():
    blocks, _ = get_adapter("/v1/chat/completions").parse_request(CHAT_REQUEST)
    assert _paths(blocks) == [
        ("tool_definition", ("tools", 0)),
        ("system_prompt", ("messages", 0, "content")),
        ("user_message", ("messages", 1, "content", 0)),
        ("other", ("messages", 1, "content", 1)),
        ("user_message", ("messages", 2, "content")),
        ("assistant_message", ("messages", 3, "content")),
        ("tool_call", ("messages", 3, "tool_calls", 0)),
        ("tool_call", ("messages", 3, "tool_calls", 1)),
        ("tool_result", ("messages", 4, "content")),
        ("tool_result", ("messages", 5, "content")),
    ]
    _assert_resolves(CHAT_REQUEST, blocks)


def test_openai_chat_legacy_functions_key_and_response_paths():
    legacy = {"functions": [{"name": "f", "parameters": {}}], "messages": []}
    blocks, _ = get_adapter("/v1/chat/completions").parse_request(legacy)
    assert _paths(blocks) == [("tool_definition", ("functions", 0))]

    response = {"choices": [{"index": 0, "message": {
        "role": "assistant", "reasoning_content": "thinking", "content": "answer",
        "tool_calls": [{"id": "c9", "function": {"name": "f", "arguments": "{}"}}],
        "function_call": {"name": "g", "arguments": "{}"},
    }}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    out, _ = get_adapter("/v1/chat/completions").parse_response(response)
    assert _paths(out) == [
        ("thinking", ("choices", 0, "message", "reasoning_content")),
        ("assistant_message", ("choices", 0, "message", "content")),
        ("tool_call", ("choices", 0, "message", "tool_calls", 0)),
        ("tool_call", ("choices", 0, "message", "function_call")),
    ]
    _assert_resolves(response, out)


def test_openai_chat_reasoning_key_and_delta_shape():
    response = {"choices": [{"index": 3, "delta": {"reasoning": "why", "content": "ok"}}], "usage": {}}
    out, _ = get_adapter("/v1/chat/completions").parse_response(response)
    # The path uses the list position of the choice, not its "index" field.
    assert _paths(out) == [
        ("thinking", ("choices", 0, "delta", "reasoning")),
        ("assistant_message", ("choices", 0, "delta", "content")),
    ]
    _assert_resolves(response, out)


RESPONSES_REQUEST = {
    "model": "m",
    "instructions": "be useful",
    "tools": [
        {"type": "function", "name": "shell", "parameters": {}},
        {"type": "namespace", "name": "collab", "tools": [
            {"type": "function", "name": "spawn", "parameters": {}},
            {"type": "function", "name": "wait", "parameters": {}},
        ]},
    ],
    "input": [
        {"role": "developer", "content": [{"type": "input_text", "text": "dev rules"}]},
        {"role": "user", "content": "string content"},
        {"role": "user", "content": [{"type": "input_text", "text": "part"}, {"type": "input_image", "image_url": "x"}]},
        {"type": "reasoning", "summary": [{"text": "sum"}], "encrypted_content": "zzz"},
        {"type": "function_call", "call_id": "k1", "name": "shell", "arguments": "{\"cmd\":\"ls\"}"},
        {"type": "function_call_output", "call_id": "k1", "output": "file.txt"},
        {"type": "compaction", "encrypted_content": "abc"},
        {"type": "additional_tools", "tools": [{"type": "function", "name": "extra", "parameters": {}}]},
        {"type": "mystery", "data": 1},
    ],
}


def test_openai_responses_request_paths():
    blocks, _ = get_adapter("/v1/responses").parse_request(RESPONSES_REQUEST)
    assert _paths(blocks) == [
        ("tool_definition", ("tools", 0)),
        ("tool_definition", ("tools", 1, "tools", 0)),
        ("tool_definition", ("tools", 1, "tools", 1)),
        ("system_prompt", ("instructions",)),
        ("system_prompt", ("input", 0, "content", 0)),
        ("user_message", ("input", 1, "content")),
        ("user_message", ("input", 2, "content", 0)),
        ("other", ("input", 2, "content", 1)),
        ("thinking", ("input", 3)),
        ("tool_call", ("input", 4)),
        ("tool_result", ("input", 5)),
        ("other", ("input", 6)),
        ("tool_definition", ("input", 7, "tools", 0)),
        ("other", ("input", 8)),
    ]
    _assert_resolves(RESPONSES_REQUEST, blocks, unlocated=())


def test_openai_responses_string_input_and_additional_tools_shapes():
    adapter = get_adapter("/v1/responses")
    document = {"input": "hello"}
    blocks, _ = adapter.parse_request(document)
    # A bare-string input is wrapped into one message whose content is the string itself.
    assert _paths(blocks) == [("user_message", ("input",))]
    _assert_resolves(document, blocks)

    document = {"input": {"role": "user", "content": "single object"}}
    blocks, _ = adapter.parse_request(document)
    assert _paths(blocks) == [("user_message", ("input", "content"))]
    _assert_resolves(document, blocks)

    as_list = {"additional_tools": [{"type": "function", "name": "a", "parameters": {}}], "input": []}
    assert _paths(adapter.parse_request(as_list)[0]) == [("tool_definition", ("additional_tools", 0))]

    wrapped = {"additional_tools": {"tools": [{"type": "function", "name": "b", "parameters": {}}]}, "input": []}
    assert _paths(adapter.parse_request(wrapped)[0]) == [("tool_definition", ("additional_tools", "tools", 0))]

    single = {"additional_tools": {"type": "function", "name": "c", "parameters": {}}, "input": []}
    assert _paths(adapter.parse_request(single)[0]) == [("tool_definition", ("additional_tools",))]


def test_openai_responses_response_paths():
    response = {"output": [
        {"type": "reasoning", "summary": [{"text": "s"}], "encrypted_content": "e"},
        {"type": "message", "content": [
            {"type": "output_text", "text": "hello"},
            {"type": "refusal", "refusal": "no"},
        ]},
        {"type": "function_call", "call_id": "f1", "name": "shell", "arguments": "{}"},
        {"type": "compaction", "encrypted_content": "q"},
        {"type": "weird"},
    ], "usage": {"input_tokens": 1, "output_tokens": 2}}
    out, _ = get_adapter("/v1/responses").parse_response(response)
    assert _paths(out) == [
        ("thinking", ("output", 0)),
        ("assistant_message", ("output", 1, "content", 0)),
        ("assistant_message", ("output", 1, "content", 1)),
        ("tool_call", ("output", 2)),
        ("other", ("output", 3)),
        ("other", ("output", 4)),
    ]
    _assert_resolves(response, out)


def test_ollama_paths():
    adapter = get_adapter("/api/chat")
    request = {"messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": ""},      # skipped, index must still count
        {"role": "user", "content": "q"},
    ]}
    blocks, _ = adapter.parse_request(request)
    assert _paths(blocks) == [
        ("system_prompt", ("messages", 0, "content")),
        ("user_message", ("messages", 2, "content")),
    ]
    _assert_resolves(request, blocks)

    response = {"message": {"role": "assistant", "thinking": "t", "content": "c"}}
    out, _ = adapter.parse_response(response)
    assert _paths(out) == [
        ("thinking", ("message", "thinking")),
        ("assistant_message", ("message", "content")),
    ]
    _assert_resolves(response, out)


def test_block_make_defaults_to_no_location_and_roundtrips_through_the_database(tmp_path):
    from datetime import datetime, timezone

    from contextspy.analysis.blocks import BlockType, Direction
    from contextspy.db import crud
    from contextspy.db.database import get_db, init_db

    assert Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "x").json_path is None
    init_db(tmp_path / "paths.db")
    located = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "x", json_path=("messages", 0, "content"))
    unlocated = Block.make(Direction.INPUT, BlockType.USER_MESSAGE, "y")
    with get_db() as db:
        crud.create_request(db, {
            "id": "r", "timestamp": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "provider": "openai", "endpoint": "/v1/chat/completions",
        })
        crud.insert_blocks(db, "r", [located, unlocated])
    with get_db() as db:
        rows = crud.get_blocks(db, "r")
    assert [r["json_path"] for r in rows] == [["messages", 0, "content"], None]
