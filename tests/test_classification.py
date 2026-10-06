"""Block source keys, activity labels and request purpose (analysis/sources|activity|purpose)."""
import json
from types import SimpleNamespace

import pytest

from contextspy.analysis import sources
from contextspy.analysis.activity import activity_for
from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import AnalyzedRequest, Block, BlockSnapshot, BlockType, Direction, Usage
from contextspy.analysis.purpose import (
    CLASSIFIER_VERSION,
    PurposeResult,
    classify_request,
    register_purpose_detector,
)
from contextspy.analysis.purpose import _DETECTORS
from contextspy.analysis.sources import mcp_key, resolve_sources, shell_program

try:
    from contextspy.proxy.addon import ContextSpyAddon  # noqa: F401
    _HAS_ADDON = True
except ImportError:
    _HAS_ADDON = False

IN, OUT = Direction.INPUT, Direction.OUTPUT


def _keys(blocks, agent=None):
    return [info.key for info in resolve_sources(blocks, agent=agent)]


# --------------------------------------------------------------------------- shell_program

@pytest.mark.parametrize("command, expected", [
    ("git diff --stat | head", "git"),
    ("cd /a/b && rg -n foo src", "rg"),
    ("cd \"a;b\" && git status", "git"),
    ("FOO=1 BAR=2 pytest -q", "pytest"),
    ("sudo -u someone apt install x", "apt"),
    ("/usr/bin/env python3 x.py", "python3"),
    ("env -u A B=1 node x.js", "node"),
    ("time -p make", "make"),
    ("(cd x && make)", "make"),
    ("./gradlew build", "gradlew"),
    ("echo 'a && b' && ls", "echo"),
    ("ls $(pwd)", "ls"),
    ("", None),
    ("cd x", None),
    ("sudo", None),
    ("export A=1", None),
    ("\"unterminated quote", None),
])
def test_shell_program(command, expected):
    assert shell_program(command) == expected


def test_shell_program_rejects_non_program_words():
    assert shell_program("$(whoami) now") is None
    assert shell_program("a" * 200) is None


# --------------------------------------------------------------------------- baseline keys

def test_baseline_keys_by_block_type():
    blocks = [
        Block.make(IN, BlockType.SYSTEM_PROMPT, "s"),
        Block.make(IN, BlockType.USER_MESSAGE, "u", message_index=0),
        Block.make(IN, BlockType.ASSISTANT_MESSAGE, "a", message_index=1),
        Block.make(IN, BlockType.ASSISTANT_PREFILL, "p", message_index=2),
        Block.make(IN, BlockType.THINKING, "t", message_index=1),
        Block.make(IN, BlockType.OTHER, "o", message_index=1),
        Block.make(IN, BlockType.TOOL_DEFINITION, "{}", tool_name="Read"),
    ]
    assert _keys(blocks) == [
        "system", "user", "assistant", "assistant", "reasoning", "other", "tool:Read",
    ]


@pytest.mark.parametrize("name, expected", [
    ("mcp__github__create_issue", "mcp:github/create_issue"),
    ("mcp__claude_ai_Claude_Docs__batch", "mcp:claude_ai_Claude_Docs/batch"),
    ("mcp__srv__tool__with__underscores", "mcp:srv/tool__with__underscores"),
    ("mcp__broken", None),
    ("mcp____tool", None),
    ("Read", None),
    (None, None),
])
def test_mcp_key(name, expected):
    assert mcp_key(name) == expected


def test_tool_blocks_use_mcp_or_generic_keys_and_unknown_when_nameless():
    blocks = [
        Block.make(IN, BlockType.TOOL_DEFINITION, "{}", tool_name="mcp__fs__read"),
        Block.make(IN, BlockType.TOOL_CALL, "{}", message_index=1, tool_name="Read", tool_call_id="c1"),
        Block.make(IN, BlockType.TOOL_CALL, "{}", message_index=1, tool_call_id="c2"),
    ]
    assert _keys(blocks) == ["mcp:fs/read", "tool:Read", "tool:unknown"]


def test_results_inherit_the_key_of_their_call_but_definitions_do_not():
    blocks = [
        Block.make(IN, BlockType.TOOL_DEFINITION, "{}", tool_name="Bash"),
        Block.make(IN, BlockType.TOOL_CALL, json.dumps({"command": "git status"}),
                   message_index=1, tool_name="Bash", tool_call_id="c1"),
        Block.make(IN, BlockType.TOOL_RESULT, "clean", message_index=2, tool_name="Bash", tool_call_id="c1"),
        Block.make(IN, BlockType.TOOL_RESULT, "orphan", message_index=2, tool_name="Read", tool_call_id="gone"),
        Block.make(IN, BlockType.TOOL_RESULT, "nameless", message_index=2, tool_call_id="gone2"),
    ]
    assert _keys(blocks) == ["tool:Bash", "bash:git", "bash:git", "tool:Read", "tool:unknown"]


# --------------------------------------------------------------------------- Bash parser

@pytest.mark.parametrize("arguments, expected", [
    ({"command": "git diff --stat | head", "description": "d", "run_in_background": False}, "bash:git"),
    ({"cmd": "cd src && rg foo"}, "bash:rg"),
    ({"command": "VAR=1 pytest -q tests/"}, "bash:pytest"),
    ({"command": ""}, "tool:Bash"),
    ({"command": 7}, "tool:Bash"),
    ({"other": "x"}, "tool:Bash"),
    ([1, 2], "tool:Bash"),
])
def test_bash_parser(arguments, expected):
    call = Block.make(IN, BlockType.TOOL_CALL, json.dumps(arguments), message_index=1,
                      tool_name="Bash", tool_call_id="c")
    assert _keys([call]) == [expected]


def test_bash_parser_ignores_non_json_content_and_lowercase_tool_name_namespace():
    call = Block.make(IN, BlockType.TOOL_CALL, "not json", message_index=1, tool_name="Bash")
    assert _keys([call]) == ["tool:Bash"]
    call = Block.make(IN, BlockType.TOOL_CALL, json.dumps({"command": "ls"}), message_index=1, tool_name="bash")
    assert _keys([call]) == ["bash:ls"]


def test_parsers_never_record_arguments():
    secret = "hunter2-secret-token"
    call = Block.make(IN, BlockType.TOOL_CALL,
                      json.dumps({"command": f"curl -H 'Authorization: {secret}' https://x.test/{secret}"}),
                      message_index=1, tool_name="Bash", tool_call_id="c")
    (info,) = resolve_sources([call], agent=None)
    assert info.key == "bash:curl" and info.detail is None
    codex = Block.make(IN, BlockType.TOOL_CALL,
                       f'const r = await tools.exec_command({{cmd:"curl -u {secret} x"}}); text(r.output);',
                       message_index=1, tool_name="exec", tool_call_id="c2")
    (info,) = resolve_sources([codex], agent="codex")
    assert info.key == "exec:curl" and secret not in repr(info)


# --------------------------------------------------------------------------- Codex exec parser

def _exec(content, tool_name="exec"):
    return Block.make(IN, BlockType.TOOL_CALL, content, message_index=1, tool_name=tool_name, tool_call_id="c")


@pytest.mark.parametrize("snippet, key, detail", [
    ("const r = await tools.exec_command({cmd:\"sed -n '290,335p' src/app.ts; sed -n '1,5p' x\","
     "workdir:\"/repo\",max_output_tokens:3300}); text(r.output);", "exec:sed", {"files": ["src/app.ts", "x"]}),
    ('const r=await tools.exec_command({cmd:"rg -n \\"foo bar\\" src",workdir:"/r"});', "exec:rg", None),
    ("const r = await tools.exec_command({cmd:'git status --short'});", "exec:git", None),
    ("const r = await tools.exec_command({cmd:`cd app && npm test`});", "exec:npm", None),
    ('const patch = "*** Begin Patch\\n*** Update File: x.py\\n*** End Patch"; await tools.apply_patch(patch);',
     "exec:apply_patch", None),
    ('const patch="*** Begin Patch\\n*** End Patch"; text(patch)', "exec:apply_patch", None),
    ('const r = await Promise.allSettled([ tools.exec_command({cmd:"git status"}), '
     'tools.exec_command({cmd:"rg -n foo src"}), tools.exec_command({cmd:"git diff"}) ]);',
     "exec:multi", {"calls": ["git", "rg"]}),
    ("text(await tools.web_search({q:\"x\"}))", "exec:web_search", None),
    ("const cmd = build(); await tools.exec_command({cmd});", "exec:js", None),
    ("const x = `a ${b}`; await tools.exec_command({cmd:`echo ${x}`});", "exec:js", None),
    ("1 + 1", "exec:js", None),
    ("", None, None),
])
def test_codex_exec_parser(snippet, key, detail):
    infos = resolve_sources([_exec(snippet)], agent="codex")
    if key is None:  # empty content has nothing to parse: baseline
        assert infos[0].key == "tool:exec"
    else:
        assert (infos[0].key, infos[0].detail) == (key, detail)


def test_codex_parser_only_applies_to_the_codex_agent_and_its_tool_names():
    snippet = 'await tools.exec_command({cmd:"git status"})'
    assert _keys([_exec(snippet)], agent="claude_code") == ["tool:exec"]
    assert _keys([_exec(snippet)], agent=None) == ["tool:exec"]
    assert _keys([_exec(snippet, tool_name="js")], agent="codex") == ["exec:git"]
    assert _keys([_exec(snippet, tool_name="other")], agent="codex") == ["tool:other"]


def _snapshot_call(content):
    # Built directly: tokenizing multi-megabyte strings is not what is under test.
    return BlockSnapshot(direction="input", block_type="tool_call", message_index=1,
                         tool_name="exec", tool_call_id="c", content=content)


def test_codex_parser_survives_huge_and_hostile_snippets():
    huge = "x" * 3_000_000 + 'tools.exec_command({cmd:"git status"})'
    assert _keys([_snapshot_call(huge)], agent="codex") == ["exec:js"]  # beyond the scan cap: not parsed
    hostile = 'tools.exec_command({cmd:"' + "\\" * 10_000
    assert _keys([_snapshot_call(hostile)], agent="codex") == ["exec:js"]


def test_purged_or_missing_content_falls_back_to_baseline():
    snapshot = BlockSnapshot(direction="input", block_type="tool_call", tool_name="Bash",
                             tool_call_id="c", message_index=1, content=None)
    assert _keys([snapshot]) == ["tool:Bash"]


def test_a_failing_parser_falls_back_to_baseline(monkeypatch):
    def boom(block, agent):
        raise RuntimeError("bad parser")

    monkeypatch.setattr(sources, "_PARSERS", [(None, frozenset({"Bash"}), boom)])
    call = Block.make(IN, BlockType.TOOL_CALL, json.dumps({"command": "ls"}), message_index=1, tool_name="Bash")
    assert _keys([call]) == ["tool:Bash"]


def test_register_source_parser_extends_the_registry(monkeypatch):
    monkeypatch.setattr(sources, "_PARSERS", [])
    sources.register_source_parser(
        agents=frozenset({"myagent"}), tool_names=frozenset({"shell"}),
        parser=lambda block, agent: sources.SourceInfo("shell:custom"),
    )
    call = Block.make(IN, BlockType.TOOL_CALL, "x", message_index=1, tool_name="shell")
    assert _keys([call], agent="myagent") == ["shell:custom"]
    assert _keys([call], agent="other") == ["tool:shell"]


# --------------------------------------------------------------------------- activity

@pytest.mark.parametrize("key, expected", [
    (None, None), ("", None), ("system", None), ("user", None), ("assistant", None), ("reasoning", None),
    ("other", "other"),
    ("mcp:github/create_issue", "mcp"),
    ("tool:Read", "read"), ("tool:read_file", "read"), ("tool:Grep", "search"), ("tool:Glob", "search"),
    ("tool:Edit", "edit"), ("tool:MultiEdit", "edit"), ("tool:Bash", "command"), ("tool:shell", "command"),
    ("tool:WebFetch", "web"), ("tool:Task", "orchestration"), ("tool:spawn_agent", "orchestration"),
    ("tool:SomethingNew", "other"), ("tool:unknown", "other"),
    ("exec:rg", "search"), ("bash:grep", "search"), ("exec:sed", "read"), ("bash:ls", "read"),
    ("exec:git", "vcs"), ("bash:gh", "vcs"), ("bash:pytest", "test"), ("exec:apply_patch", "edit"),
    ("exec:multi", "command"), ("exec:js", "command"), ("bash:make", "command"), ("custom:thing", "command"),
])
def test_activity_for(key, expected):
    assert activity_for(key) == expected


# --------------------------------------------------------------------------- purpose

def _classify(request_body, adapter_path, response_body=None, *, agent=None, has_response=None):
    adapter = get_adapter(adapter_path)
    inputs, call_map = adapter.parse_request(request_body)
    outputs, usage = adapter.parse_response(response_body) if response_body is not None else ([], Usage())
    analyzed = AnalyzedRequest(model="m", input_blocks=inputs, output_blocks=outputs,
                               usage=usage, tool_call_map=call_map)
    result = classify_request(
        analyzed, agent=agent, has_response=response_body is not None if has_response is None else has_response,
    )
    return result, analyzed


def test_anthropic_user_turn_and_tool_continuation():
    base = [{"role": "user", "content": "do it"}]
    result, _ = _classify({"messages": base}, "/v1/messages")
    assert (result.purpose, result.classifier_version) == ("user_turn", CLASSIFIER_VERSION)
    assert result.purpose_detail is None  # no response supplied, nothing else to say

    with_call = base + [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "body"}]},
    ]
    result, _ = _classify({"messages": with_call}, "/v1/messages")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail == {"trailing_tool_results": ["Read"], "has_user_text": False}


def test_anthropic_result_plus_user_text_in_one_message():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            {"type": "text", "text": "<reminder>"},
        ]},
    ]
    result, _ = _classify({"messages": messages}, "/v1/messages")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail["has_user_text"] is True


def test_openai_chat_parallel_tool_messages_are_one_trailing_run():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "b", "function": {"name": "grep", "arguments": "{}"}},
            {"id": "c", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "1"},
        {"role": "tool", "tool_call_id": "b", "content": "2"},
        {"role": "tool", "tool_call_id": "c", "content": "3"},
    ]
    result, _ = _classify({"messages": messages}, "/v1/chat/completions")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail["trailing_tool_results"] == ["read_file", "grep"]


def test_openai_responses_function_call_output_and_user_turn():
    items = [
        {"role": "user", "content": "go"},
        {"type": "function_call", "call_id": "k", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "k", "output": "done"},
    ]
    result, _ = _classify({"input": items}, "/v1/responses")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail["trailing_tool_results"] == ["shell"]
    result, _ = _classify({"input": items[:1]}, "/v1/responses")
    assert result.purpose == "user_turn"
    result, _ = _classify({"input": "just a string"}, "/v1/responses")
    assert result.purpose == "user_turn"


def test_tool_result_whose_call_is_not_in_the_request_still_classifies():
    # Server-held context (e.g. previous_response_id): only the new output item is sent.
    result, _ = _classify({"input": [{"type": "function_call_output", "call_id": "x", "output": "r"}]},
                          "/v1/responses")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail == {"has_user_text": False}


def test_trailing_instruction_messages_do_not_hide_the_real_tail():
    # A system-role message appended after a tool result (Anthropic) is not the conversation's last turn.
    anthropic = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
        {"role": "system", "content": "extra instructions"},
    ]
    result, _ = _classify({"messages": anthropic}, "/v1/messages")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail["trailing_tool_results"] == ["Read"]

    # A developer-role item after the user's message (Responses) likewise.
    items = [{"role": "user", "content": "go"}, {"role": "developer", "content": "be careful"}]
    result, _ = _classify({"input": items}, "/v1/responses")
    assert result.purpose == "user_turn"

    # And a request made only of instructions has no conversational tail at all.
    result, _ = _classify({"input": [{"role": "developer", "content": "rules"}]}, "/v1/responses")
    assert result.purpose == "unknown"


def test_reasoning_items_after_a_tool_result_do_not_hide_it():
    items = [
        {"role": "user", "content": "go"},
        {"type": "function_call", "call_id": "k", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "k", "output": "ok"},
        {"type": "reasoning", "summary": [], "encrypted_content": "zzz"},
    ]
    result, _ = _classify({"input": items}, "/v1/responses")
    assert result.purpose == "tool_continuation"
    assert result.purpose_detail["trailing_tool_results"] == ["shell"]


def test_compaction_trigger_item_is_labelled_compaction():
    items = [
        {"role": "user", "content": "go"},
        {"type": "function_call", "call_id": "k", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "k", "output": "ok"},
        {"type": "compaction_trigger"},
    ]
    result, _ = _classify({"input": items}, "/v1/responses")
    assert result.purpose == "compaction"
    # The compacted history coming back (a "compaction" item) is not itself a trigger.
    result, _ = _classify({"input": items[:3] + [{"type": "compaction", "encrypted_content": "abc"}]}, "/v1/responses")
    assert result.purpose == "unknown"


def test_ollama_user_turn():
    result, _ = _classify({"messages": [{"role": "user", "content": "hi"}]}, "/api/chat")
    assert result.purpose == "user_turn"


def test_unknown_when_there_is_nothing_to_go_on():
    result, _ = _classify({"messages": [{"role": "system", "content": "s"}]}, "/v1/messages")
    assert (result.purpose, result.classifier_version) == ("unknown", CLASSIFIER_VERSION)
    # No blocks at all means the request was not parsed: it must stay unclassified, not "unknown".
    result, _ = _classify({"messages": []}, "/v1/messages")
    assert result is None
    prefill = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "The answer"}]}
    result, _ = _classify(prefill, "/v1/messages")
    assert result.purpose == "unknown"


@pytest.mark.parametrize("output, kind, tools", [
    ([{"type": "text", "text": "done"}], "final_text", None),
    ([{"type": "tool_use", "id": "t", "name": "Edit", "input": {}}], "tool_calls", ["Edit"]),
    ([{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t", "name": "Edit", "input": {}},
      {"type": "tool_use", "id": "u", "name": "Edit", "input": {}}], "mixed", ["Edit"]),
    ([{"type": "thinking", "thinking": "hmm"}], "empty", None),
    ([], "empty", None),
])
def test_response_kind(output, kind, tools):
    result, _ = _classify({"messages": [{"role": "user", "content": "q"}]}, "/v1/messages",
                          {"content": output, "usage": {}})
    expected = {"kind": kind, **({"tool_calls": tools} if tools else {})}
    assert result.purpose_detail["response"] == expected


def test_no_response_key_without_a_response():
    result, _ = _classify({"messages": [{"role": "user", "content": "q"}]}, "/v1/messages",
                          {"content": [], "usage": {}}, has_response=False)
    assert result.purpose_detail is None


def test_classification_stamps_source_keys_and_attrs_on_blocks():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git log"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "log"}]},
    ]
    _, analyzed = _classify({"messages": messages, "tools": [{"name": "Bash"}]}, "/v1/messages",
                            {"content": [{"type": "tool_use", "id": "u", "name": "mcp__s__t", "input": {}}],
                             "usage": {}})
    assert [b.source_key for b in analyzed.input_blocks] == ["tool:Bash", "user", "bash:git", "bash:git"]
    assert [b.source_key for b in analyzed.output_blocks] == ["mcp:s/t"]


def test_codex_multi_call_detail_lands_in_block_attrs():
    snippet = ('await Promise.allSettled([tools.exec_command({cmd:"git status"}), '
               'tools.exec_command({cmd:"rg x"})])')
    request = {"input": [{"type": "custom_tool_call", "call_id": "c", "name": "exec", "input": snippet}]}
    _, analyzed = _classify(request, "/v1/responses", agent="codex")
    (call,) = analyzed.input_blocks
    assert call.source_key == "exec:multi"
    assert call.attrs["source"] == {"calls": ["git", "rg"]}


def test_classification_failure_never_raises_and_leaves_fields_empty(monkeypatch):
    from contextspy.analysis import purpose

    monkeypatch.setattr(purpose, "resolve_sources", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    result, _ = _classify({"messages": [{"role": "user", "content": "q"}]}, "/v1/messages")
    assert (result.purpose, result.purpose_detail, result.classifier_version) == (None, None, CLASSIFIER_VERSION)
    assert result.to_db_fields() == {"purpose": None, "purpose_detail": None,
                                     "classifier_version": CLASSIFIER_VERSION}


def test_registered_detector_overrides_the_baseline_for_its_agent(monkeypatch):
    monkeypatch.setattr("contextspy.analysis.purpose._DETECTORS", [])
    register_purpose_detector(
        agents=frozenset({"special"}),
        detector=lambda inputs: PurposeResult("housekeeping", {"note": "title"}),
    )
    body = {"messages": [{"role": "user", "content": "q"}]}
    result, _ = _classify(body, "/v1/messages", {"content": [], "usage": {}}, agent="special")
    assert result.purpose == "housekeeping"
    assert result.purpose_detail == {"response": {"kind": "empty"}, "note": "title"}
    other, _ = _classify(body, "/v1/messages", agent="someone-else")
    assert other.purpose == "user_turn"


def test_a_failing_detector_falls_back_to_the_baseline(monkeypatch):
    def boom(inputs):
        raise RuntimeError("x")

    monkeypatch.setattr("contextspy.analysis.purpose._DETECTORS", [(None, boom)])
    result, _ = _classify({"messages": [{"role": "user", "content": "q"}]}, "/v1/messages")
    assert result.purpose == "user_turn"


def test_to_db_fields_serialises_detail():
    result, _ = _classify({"messages": [{"role": "user", "content": "q"}]}, "/v1/messages",
                          {"content": [{"type": "text", "text": "a"}], "usage": {}})
    fields = result.to_db_fields()
    assert fields["purpose"] == "user_turn"
    assert json.loads(fields["purpose_detail"]) == {"response": {"kind": "final_text"}}


def test_snapshot_views_classify_like_blocks():
    """The backfill classifies stored rows: BlockSnapshot must behave exactly like Block."""
    from contextspy.analysis.purpose import PurposeInputs, derive_purpose

    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
    ]
    blocks, _ = get_adapter("/v1/messages").parse_request({"messages": messages})
    snapshots = [BlockSnapshot(b.direction, b.block_type, b.message_index, b.tool_name,
                               b.tool_call_id, dict(b.attrs), b.content) for b in blocks]
    a = derive_purpose(PurposeInputs("x", blocks, [], False))
    b = derive_purpose(PurposeInputs("x", snapshots, [], False))
    assert a == b
    assert _keys(blocks, "x") == _keys(snapshots, "x")


# --------------------------------------------------------------------------- capture end to end

@pytest.mark.skipif(not _HAS_ADDON, reason="mitmproxy not installed")
class TestCaptureStampsClassification:
    @staticmethod
    def _flow(request_text, response_text, path="/v1/responses", host="api.openai.com", ua="test"):
        request = SimpleNamespace(
            pretty_host=host, port=443, path=path, headers={"user-agent": ua},
            get_text=lambda: request_text,
        )
        response = SimpleNamespace(
            status_code=200, headers={"content-type": "application/json"},
            get_text=lambda: response_text,
        )
        return SimpleNamespace(
            id="flow-1", request=request, response=response, websocket=None,
            metadata={"contextspy_request_body": request_text}, error=None,
        )

    def test_new_request_persists_purpose_sources_and_paths(self, tmp_path):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.proxy.addon import ContextSpyAddon

        init_db(tmp_path / "classified.db")
        request_text = json.dumps({"model": "m", "input": [{"role": "user", "content": "run it"}]})
        response_text = json.dumps({
            "id": "resp-1", "model": "m",
            "output": [{"type": "function_call", "call_id": "k", "name": "shell", "arguments": "{}"}],
            "usage": {"input_tokens": 5, "output_tokens": 1},
        })
        ContextSpyAddon(provider_override="openai")._handle_response(self._flow(request_text, response_text))

        with get_db() as db:
            (row,) = crud.list_requests(db)
            request = row.to_dict(include_raw=False)
            blocks = crud.get_blocks(db, row.id)
        assert request["purpose"] == "user_turn"
        assert request["classifier_version"] == CLASSIFIER_VERSION
        assert request["purpose_detail"] == {"response": {"kind": "tool_calls", "tool_calls": ["shell"]}}
        by_direction = {b["direction"]: b for b in blocks}
        assert by_direction["input"]["source_key"] == "user"
        assert by_direction["input"]["json_path"] == ["input", 0, "content"]
        assert by_direction["output"]["source_key"] == "tool:shell"
        assert by_direction["output"]["activity"] == "command"
        assert by_direction["output"]["json_path"] == ["output", 0]

    def test_unparsed_request_stays_unclassified(self, tmp_path, monkeypatch):
        from contextspy.analysis.capture import CanonicalResponse
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.proxy import addon as addon_module

        class FailingAdapter:
            format_id = "test"
            stream_format = "sse"

            def reconstruct_response(self, events, *, transport):
                return CanonicalResponse(payload={}, transport=transport, events=events)

            def parse_request(self, request):
                raise ValueError("boom")

            def parse_response(self, response):
                raise ValueError("boom")

        init_db(tmp_path / "unparsed.db")
        monkeypatch.setattr(addon_module, "get_adapter", lambda endpoint: FailingAdapter())
        flow = self._flow(json.dumps({"model": "m", "input": "x"}), "{}")
        addon_module.ContextSpyAddon(provider_override="openai")._handle_response(flow)
        with get_db() as db:
            (row,) = crud.list_requests(db)
            assert (row.purpose, row.purpose_detail, row.classifier_version) == (None, None, None)

    def test_classification_error_does_not_lose_the_capture(self, tmp_path, monkeypatch):
        from contextspy.db import crud
        from contextspy.db.database import get_db, init_db
        from contextspy.analysis import purpose
        from contextspy.proxy.addon import ContextSpyAddon

        init_db(tmp_path / "failing_classifier.db")
        monkeypatch.setattr(purpose, "derive_purpose", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        request_text = json.dumps({"model": "m", "input": "hello"})
        response_text = json.dumps({"id": "r2", "model": "m", "output": [], "usage": {}})
        ContextSpyAddon(provider_override="openai")._handle_response(self._flow(request_text, response_text))
        with get_db() as db:
            (row,) = crud.list_requests(db)
            assert row.tokens_total_input > 0
            assert row.purpose is None and row.classifier_version == CLASSIFIER_VERSION
