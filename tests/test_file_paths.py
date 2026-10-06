"""Plan 4a: the file a read/edit tool call targets (analysis/paths.py, analysis/sources.py, schema v10)."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, update

from contextspy.analysis import paths as paths_module
from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import AnalyzedRequest, Block, BlockType, Direction, Usage
from contextspy.analysis.paths import normalize_file_path, patch_file_paths, structured_file_path
from contextspy.analysis.purpose import CLASSIFIER_VERSION, classify_request
from contextspy.analysis.sources import resolve_sources, shell_file_paths
from contextspy.db import crud, migrations
from contextspy.db.database import get_db, init_db
from contextspy.db.models import BlockContent, BlockRecord, Request

IN = Direction.INPUT
_TS = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _call(content, tool_name, call_id="c"):
    return Block.make(IN, BlockType.TOOL_CALL, content, message_index=1, tool_name=tool_name, tool_call_id=call_id)


def _result(tool_name, call_id="c"):
    return Block.make(IN, BlockType.TOOL_RESULT, "file text", message_index=2, tool_name=tool_name, tool_call_id=call_id)


# --------------------------------------------------------------------------- normalize_file_path

@pytest.mark.parametrize("raw, expected", [
    ("src/app.py", "src/app.py"),
    ("  /a/b.py \n", "/a/b.py"),
    ("'/a/b c.py'", "/a/b c.py"),
    ('"a.py"', "a.py"),
    ("/a//b///c.py", "/a/b/c.py"),
    ("Src/App.PY", "Src/App.PY"),
    ("~/x.py", "~/x.py"),
    ("app/[id]/page.tsx", "app/[id]/page.tsx"),
    ("a" * 1024, "a" * 1024),
    ("", None),
    ("   ", None),
    ("a" * 1025, None),
    ("a\nb", None),
    ("a\x00b", None),
    ("https://x.test/a.py", None),
    ("src/*.py", None),
    ("a?.py", None),
    ("src/", None),
    (".", None),
    ("..", None),
    ("/", None),
    (None, None),
    (3, None),
    ({"path": "x"}, None),
])
def test_normalize_file_path(raw, expected):
    assert normalize_file_path(raw) == expected


# --------------------------------------------------------------------------- structured arguments

@pytest.mark.parametrize("key", ["file_path", "filepath", "path", "filename", "file", "notebook_path"])
def test_every_known_key_is_read(key):
    assert structured_file_path("Read", json.dumps({key: "/a/b.py"})) == "/a/b.py"


def test_key_precedence_and_other_arguments_never_leak():
    content = json.dumps({"path": "/second", "file_path": "/first", "old_string": "SECRET", "command": "x"})
    assert structured_file_path("Edit", content) == "/first"
    assert structured_file_path("Edit", json.dumps({"path": {"nested": "/x"}, "file": "/y"})) == "/y"


@pytest.mark.parametrize("value", [
    "https://host/a", "src/*.py", "a\nb", "a" * 2000, 12, ["x"], {"a": 1}, "",
])
def test_structured_rejections(value):
    assert structured_file_path("Read", json.dumps({"file_path": value})) is None


@pytest.mark.parametrize("tool", ["Read", "read_file", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor",
                                  "str_replace_based_edit_tool", "view", "open_file"])
def test_known_file_tools(tool):
    assert structured_file_path(tool, '{"path": "/a.py"}') == "/a.py"


@pytest.mark.parametrize("tool", ["Bash", "Grep", "Glob", "WebFetch", "mcp__fs__read_file", "list_dir", "", None])
def test_other_tools_are_ignored(tool):
    assert structured_file_path(tool, '{"path": "/a.py", "file_path": "/b.py"}') is None


def test_non_json_or_non_object_arguments():
    assert structured_file_path("Read", "not json") is None
    assert structured_file_path("Read", '["/a.py"]') is None
    assert structured_file_path("Read", None) is None


# --------------------------------------------------------------------------- shell

@pytest.mark.parametrize("command, expected", [
    ("sed -n '1,5p' a/b.py", ["a/b.py"]),
    ("sed -n 1,5p a b", ["a", "b"]),
    ("sed -e 's/a/b/' -n f.txt", ["f.txt"]),
    ("head -n 20 f", ["f"]),
    ("head -20 f", ["f"]),
    ("tail -n +5 g.log", ["g.log"]),
    ("cat a b", ["a", "b"]),
    ("cat -n src/x.py | head", ["src/x.py"]),
    ("cd repo && nl -ba pkg/m.py | sed -n '1,9p'", ["pkg/m.py"]),
    ("wc -l one.txt", ["one.txt"]),
    ("stat -c %s big.bin", ["big.bin"]),
    ("bat --style=plain -r 1:5 README.md", ["README.md"]),
    ("sudo cat /etc/hosts", ["/etc/hosts"]),
    ("cat 'with space.txt' 2>/dev/null", ["with space.txt"]),
    ("cat a; cat a; head b", ["a", "b"]),
    ("sed -i 's/a/b/' f.py", []),
    ("sed 's/a/b/' f.py", []),
    ("rg foo src", []),
    ("grep -rn token .", []),
    ("curl -H 'Authorization: hunter2' https://x.test/secret", []),
    ("git show HEAD:a.py", []),
    ("find . -name '*.py'", []),
    ("cat $HOME/x $(pwd)/y", []),
    ("cat src/*.py", []),
    ("cat > out.txt <<EOF\nhi\nEOF", []),
    ("cat -", []),
    ("", []),
])
def test_shell_file_paths(command, expected):
    assert shell_file_paths(command) == expected


def test_shell_file_paths_caps_the_list():
    files = shell_file_paths("cat " + " ".join(f"f{i}.py" for i in range(50)))
    assert len(files) == 20 and files[0] == "f0.py"


def test_bash_call_gets_file_and_extra_files_in_detail():
    (info,) = resolve_sources([_call(json.dumps({"command": "cat a.py b.py"}), "Bash")], agent=None)
    assert (info.key, info.file_path, info.detail) == ("bash:cat", "a.py", {"files": ["a.py", "b.py"]})
    (info,) = resolve_sources([_call(json.dumps({"command": "sed -n '1,5p' a.py"}), "Bash")], agent=None)
    assert (info.file_path, info.detail) == ("a.py", None)


def test_secret_arguments_still_never_leak():
    secret = "hunter2-secret-token"
    call = _call(json.dumps({"command": f"curl -H 'Authorization: {secret}' https://x.test/{secret}"}), "Bash")
    (info,) = resolve_sources([call], agent=None)
    assert info.file_path is None and secret not in repr(info)
    codex = _call(f'const r = await tools.exec_command({{cmd:"curl -u {secret} x"}});', "exec")
    (info,) = resolve_sources([codex], agent="codex")
    assert info.file_path is None and secret not in repr(info)


# --------------------------------------------------------------------------- apply_patch and Codex snippets

PATCH = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-SECRET old\n+SECRET new\n*** Add File: b.txt\n+x\n*** Delete File: c.txt\n*** End Patch"


def test_patch_headers_only():
    assert patch_file_paths(PATCH) == ["src/a.py", "b.txt", "c.txt"]
    assert patch_file_paths("no patch here") == []
    assert patch_file_paths(None) == []


def test_apply_patch_tool_and_json_wrapped_patch():
    for content in (PATCH, json.dumps({"input": PATCH})):
        (info,) = resolve_sources([_call(content, "apply_patch")], agent=None)
        assert info.file_path == "src/a.py"
        assert info.detail == {"files": ["src/a.py", "b.txt", "c.txt"]}
        assert "SECRET" not in repr(info)


def test_codex_snippets():
    snippet = 'await tools.exec_command({cmd:"sed -n \'1,9p\' pkg/m.py"}); await tools.exec_command({cmd:"rg x"});'
    (info,) = resolve_sources([_call(snippet, "exec")], agent="codex")
    assert (info.key, info.file_path) == ("exec:multi", "pkg/m.py")
    patch_snippet = 'const patch = "*** Begin Patch\\n*** Update File: x/y.py\\n@@\\n+SECRET\\n*** End Patch"; await tools.apply_patch(patch);'
    (info,) = resolve_sources([_call(patch_snippet, "exec")], agent="codex")
    assert (info.key, info.file_path) == ("exec:apply_patch", "x/y.py")
    assert "SECRET" not in repr(info)
    # Another agent's tool called "exec" is not parsed as Codex JavaScript.
    (info,) = resolve_sources([_call(snippet, "exec")], agent="other")
    assert info.file_path is None


# --------------------------------------------------------------------------- inheritance

def test_results_inherit_the_call_file_and_definitions_have_none():
    blocks = [
        Block.make(IN, BlockType.TOOL_DEFINITION, '{"name": "Read"}', tool_name="Read"),
        _call(json.dumps({"file_path": "/a.py"}), "Read", "t1"),
        _result("Read", "t1"),
        _call(json.dumps({"command": "git status"}), "Bash", "t2"),
        _result("Bash", "t2"),
        Block.make(IN, BlockType.USER_MESSAGE, "hi", message_index=0),
    ]
    files = [info.file_path for info in resolve_sources(blocks, agent=None)]
    assert files == [None, "/a.py", "/a.py", None, None, None]


def test_purged_call_content_gives_no_file():
    call = _call("", "Read")
    assert resolve_sources([call, _result("Read")], agent=None)[0].file_path is None


# --------------------------------------------------------------------------- per adapter

def _classified(endpoint, doc):
    adapter = get_adapter(endpoint)
    blocks = list(adapter.parse_request(doc)[0])
    analyzed = AnalyzedRequest(model=None, input_blocks=blocks, output_blocks=[], usage=Usage())
    classify_request(analyzed, agent=None, has_response=False)
    return blocks


ADAPTER_CASES = {
    "anthropic": ("/v1/messages", {"messages": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "/p/a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "text"}]},
    ]}),
    "chat": ("/v1/chat/completions", {"messages": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "t", "type": "function",
                                              "function": {"name": "read_file", "arguments": '{"path": "/p/a.py"}'}}]},
        {"role": "tool", "tool_call_id": "t", "content": "text"},
    ]}),
    "responses": ("/v1/responses", {"input": [
        {"role": "user", "content": "go"},
        {"type": "function_call", "call_id": "t", "name": "read_file", "arguments": '{"path": "/p/a.py"}'},
        {"type": "function_call_output", "call_id": "t", "output": "text"},
    ]}),
    "ollama": ("/api/chat", {"messages": [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "/p/a.py"}}}]},
        {"role": "tool", "tool_name": "read_file", "content": "text"},
    ]}),
}


@pytest.mark.parametrize("name", [n for n in ADAPTER_CASES if n != "ollama"])
def test_call_and_result_carry_the_path_for_every_adapter(name):
    endpoint, doc = ADAPTER_CASES[name]
    blocks = _classified(endpoint, doc)
    by_type = {b.block_type: b for b in blocks if b.block_type in (BlockType.TOOL_CALL, BlockType.TOOL_RESULT)}
    assert by_type[BlockType.TOOL_CALL].file_path == "/p/a.py"
    assert by_type[BlockType.TOOL_RESULT].file_path == "/p/a.py"
    assert all(b.file_path is None for b in blocks if b.block_type == BlockType.USER_MESSAGE)


def test_ollama_adapter_emits_no_tool_blocks_so_no_paths_exist():
    # Limitation of the adapter, not of paths: Ollama history yields no tool_call/tool_result blocks.
    endpoint, doc = ADAPTER_CASES["ollama"]
    blocks = _classified(endpoint, doc)
    assert not any(b.block_type == BlockType.TOOL_CALL for b in blocks)
    assert all(b.file_path is None for b in blocks)


# --------------------------------------------------------------------------- single choke point

def test_every_stored_path_comes_from_normalize_file_path(monkeypatch):
    sentinel = "SENTINEL"
    for module in ("contextspy.analysis.paths", "contextspy.analysis.sources"):
        monkeypatch.setattr(f"{module}.normalize_file_path", lambda raw: sentinel)
    blocks = [
        _call(json.dumps({"file_path": "/a.py"}), "Read", "1"),
        _call(json.dumps({"command": "cat b.py"}), "Bash", "2"),
        _call(PATCH, "apply_patch", "3"),
        _call('await tools.exec_command({cmd:"head c.py"});', "exec", "4"),
    ]
    infos = resolve_sources(blocks, agent="codex")
    assert [i.file_path for i in infos] == [sentinel] * 4
    assert paths_module.normalize_file_path("x") == sentinel


# --------------------------------------------------------------------------- capture and migration

READ_REQUEST = ADAPTER_CASES["anthropic"][1]


def _store_unclassified(db, request_id, *, purged=False, classifier_version=None):
    adapter = get_adapter("/v1/messages")
    blocks = list(adapter.parse_request(READ_REQUEST)[0])
    crud.create_request(db, {
        "id": request_id, "timestamp": _TS, "provider": "test", "endpoint": "/v1/messages",
        "classifier_version": classifier_version,
    })
    crud.insert_blocks(db, request_id, blocks)  # source_key / file_path stay NULL


@pytest.fixture()
def database(tmp_path):
    init_db(tmp_path / "v10.db")


def test_capture_persists_file_path_on_call_and_result(database):
    blocks = list(get_adapter("/v1/messages").parse_request(READ_REQUEST)[0])
    classify_request(AnalyzedRequest(model=None, input_blocks=blocks, output_blocks=[], usage=Usage()),
                     agent="claude_code", has_response=False)
    with get_db() as db:
        crud.create_request(db, {"id": "r", "timestamp": _TS, "provider": "test", "endpoint": "/v1/messages"})
        crud.insert_blocks(db, "r", blocks)
        stored = {b["block_type"]: b for b in crud.get_blocks(db, "r")}
    assert stored["tool_call"]["file_path"] == "/p/a.py" == stored["tool_result"]["file_path"]
    assert stored["user_message"]["file_path"] is None


def test_classifier_version_was_bumped_and_v10_is_registered():
    assert CLASSIFIER_VERSION == 2 and migrations.SCHEMA_VERSION == 10
    assert 10 in migrations._DATA_MIGRATIONS


def test_v10_rederives_only_old_requests_and_keeps_attrs(database):
    with get_db() as db:
        _store_unclassified(db, "old", classifier_version=1)
        _store_unclassified(db, "new", classifier_version=CLASSIFIER_VERSION)
        call = db.execute(BlockRecord.__table__.select().where(
            BlockRecord.request_id == "old", BlockRecord.block_type == "tool_call")).one()
        db.execute(update(BlockRecord).where(BlockRecord.id == call.id).values(
            attrs=json.dumps({"cache_control": {"type": "ephemeral"}})))
        migrations._migrate_to_v10(db)
        old = {b["block_type"]: b for b in crud.get_blocks(db, "old")}
        new = {b["block_type"]: b for b in crud.get_blocks(db, "new")}
        stats = db.info["v10_backfill"]
    assert old["tool_call"]["file_path"] == "/p/a.py" == old["tool_result"]["file_path"]
    assert old["tool_call"]["attrs"] == {"cache_control": {"type": "ephemeral"}}
    assert old["tool_call"]["source_key"] == "tool:Read"
    assert all(b["file_path"] is None and b["source_key"] is None for b in new.values())
    assert stats["classified"] == 1 and stats["paths_set"] == 0  # v10 never touches JSON paths


def test_v10_is_idempotent_and_leaves_purged_calls_without_a_path(database):
    with get_db() as db:
        _store_unclassified(db, "kept", classifier_version=1)
        _store_unclassified(db, "purged", classifier_version=1)
        purged_hash = db.execute(BlockRecord.__table__.select().where(
            BlockRecord.request_id == "purged", BlockRecord.block_type == "tool_call")).one().content_hash
        migrations._migrate_to_v10(db)
        first = {r: [b["file_path"] for b in crud.get_blocks(db, r)] for r in ("kept", "purged")}
        assert db.info["v10_backfill"]["classified"] == 2
        # A second run finds nothing below the current classifier version.
        migrations._migrate_to_v10(db)
        assert db.info["v10_backfill"]["classified"] == 0
        assert first == {r: [b["file_path"] for b in crud.get_blocks(db, r)] for r in ("kept", "purged")}

    with get_db() as db:
        _store_unclassified(db, "gone", classifier_version=1)
        db.execute(delete(BlockContent))
        migrations._migrate_to_v10(db)
        assert all(b["file_path"] is None for b in crud.get_blocks(db, "gone"))
    assert purged_hash is not None
    assert first["kept"] == [None, "/p/a.py", "/p/a.py"]


def test_v9_run_directly_on_an_old_database_also_fills_file_paths(database):
    with get_db() as db:
        _store_unclassified(db, "r")
        migrations._migrate_to_v9(db)
        assert [b["file_path"] for b in crud.get_blocks(db, "r")] == [None, "/p/a.py", "/p/a.py"]
        migrations._migrate_to_v10(db)  # nothing left to do
        assert db.info["v10_backfill"]["classified"] == 0


def test_add_column_migration_is_idempotent(tmp_path):
    path = tmp_path / "x.db"
    init_db(path)
    init_db(path)
    with get_db() as db:
        columns = {row[1] for row in db.execute(__import__("sqlalchemy").text("PRAGMA table_info(blocks)"))}
        indexes = {row[1] for row in db.execute(__import__("sqlalchemy").text("PRAGMA index_list(blocks)"))}
    assert "file_path" in columns and "idx_blocks_file_path" in indexes
