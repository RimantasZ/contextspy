"""v9 backfill: purpose, source keys and JSON paths for requests captured before the upgrade."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from contextspy.analysis.adapters import get_adapter
from contextspy.analysis.blocks import AnalyzedRequest, Usage
from contextspy.analysis.purpose import CLASSIFIER_VERSION, classify_request
from contextspy.db import crud, migrations
from contextspy.db.database import get_db, init_db
from contextspy.db.models import BlockContent, BlockRecord, Request

_TS = datetime(2026, 10, 1, tzinfo=timezone.utc)

ANTHROPIC_REQUEST = {
    "model": "claude",
    "system": "be brief",
    "tools": [{"name": "Bash", "input_schema": {}}],
    "messages": [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cd src && git status"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "clean"}]},
    ],
}
ANTHROPIC_RESPONSE = {"content": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 3, "output_tokens": 2}}

CHAT_REQUEST = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]}
CHAT_RESPONSE = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}], "usage": {}}

CODEX_SNIPPET = ('await Promise.allSettled([tools.exec_command({cmd:"git status"}), '
                 'tools.exec_command({cmd:"rg foo"})])')
RESPONSES_REQUEST = {
    "instructions": "do work",
    "input": [
        {"role": "user", "content": "go"},
        {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": CODEX_SNIPPET},
        {"type": "custom_tool_call_output", "call_id": "c1", "output": "ok"},
    ],
}
RESPONSES_RESPONSE = {"output": [{"type": "function_call", "call_id": "c2", "name": "shell", "arguments": "{}"}],
                      "usage": {}}

OLLAMA_REQUEST = {"messages": [{"role": "user", "content": "hi"}]}

CASES = {
    "anthropic": ("/v1/messages", ANTHROPIC_REQUEST, ANTHROPIC_RESPONSE, "claude_code"),
    "chat": ("/v1/chat/completions", CHAT_REQUEST, CHAT_RESPONSE, None),
    "responses": ("/v1/responses", RESPONSES_REQUEST, RESPONSES_RESPONSE, "codex"),
    "ollama": ("/api/chat", OLLAMA_REQUEST, None, None),
}


def _store(db, request_id, endpoint, request_doc, response_doc=None, *, agent=None,
           keep_documents=True, classifier_version=None):
    """Insert a request the way v8 stored it: blocks exist, but no source key / JSON path / purpose."""
    adapter = get_adapter(endpoint)
    blocks = list(adapter.parse_request(request_doc)[0])
    if response_doc is not None:
        blocks += adapter.parse_response(response_doc)[0]
    for block in blocks:
        block.json_path = None
        block.source_key = None
    crud.create_request(db, {
        "id": request_id, "timestamp": _TS, "provider": "test", "endpoint": endpoint, "agent": agent,
        "canonical_request_body": json.dumps(request_doc) if keep_documents else None,
        "canonical_response_body": json.dumps(response_doc) if keep_documents and response_doc else None,
        "classifier_version": classifier_version,
    })
    crud.insert_blocks(db, request_id, blocks)


def _expected(endpoint, request_doc, response_doc, agent):
    """What capture would have stored for the same documents."""
    adapter = get_adapter(endpoint)
    inputs = list(adapter.parse_request(request_doc)[0])
    outputs, usage = adapter.parse_response(response_doc) if response_doc is not None else ([], Usage())
    classification = classify_request(
        AnalyzedRequest(model=None, input_blocks=inputs, output_blocks=outputs, usage=usage),
        agent=agent, has_response=response_doc is not None,
    )
    return classification, [*inputs, *outputs]


def _stored(db, request_id):
    """Plain values, so tests can assert after the session has closed."""
    row = db.get(Request, request_id)
    request = SimpleNamespace(
        purpose=row.purpose, purpose_detail=row.purpose_detail, classifier_version=row.classifier_version,
    )
    return request, crud.get_blocks(db, request_id)


@pytest.fixture()
def database(tmp_path):
    init_db(tmp_path / "v9.db")


@pytest.mark.parametrize("name", CASES)
def test_backfill_matches_what_capture_would_have_stored(database, name):
    endpoint, request_doc, response_doc, agent = CASES[name]
    with get_db() as db:
        _store(db, "r1", endpoint, request_doc, response_doc, agent=agent)
        migrations._migrate_to_v9(db)
    classification, expected = _expected(endpoint, request_doc, response_doc, agent)
    with get_db() as db:
        request, blocks = _stored(db, "r1")
        assert request.purpose == classification.purpose
        stored_detail = json.loads(request.purpose_detail) if request.purpose_detail else None
        assert stored_detail == classification.purpose_detail
        assert request.classifier_version == CLASSIFIER_VERSION
        by_direction = sorted(blocks, key=lambda b: (b["direction"] != "input", b["position"]))
        assert [b["source_key"] for b in by_direction] == [b.source_key for b in expected]
        assert [tuple(b["json_path"]) if b["json_path"] else None for b in by_direction] == [
            b.json_path for b in expected
        ]
        assert all(b["json_path"] is not None for b in blocks)


def test_purged_tool_call_content_falls_back_to_the_generic_key(database):
    with get_db() as db:
        _store(db, "purged", "/v1/responses", RESPONSES_REQUEST, agent="codex")
        db.execute(delete(BlockContent))  # retention removed every block's text
        migrations._migrate_to_v9(db)
        blocks = crud.get_blocks(db, "purged")
    call, result = [b for b in blocks if b["block_type"] in ("tool_call", "tool_result")]
    assert (call["source_key"], result["source_key"]) == ("tool:exec", "tool:exec")
    assert call["attrs"] == {"provider_item_type": "custom_tool_call"}  # no detail without arguments


def test_tool_call_arguments_that_are_still_stored_are_parsed(database):
    with get_db() as db:
        _store(db, "r", "/v1/responses", RESPONSES_REQUEST, agent="codex")
        migrations._migrate_to_v9(db)
        call, result = [b for b in crud.get_blocks(db, "r") if b["block_type"] in ("tool_call", "tool_result")]
    assert call["source_key"] == "exec:multi"
    assert call["attrs"] == {"provider_item_type": "custom_tool_call", "source": {"calls": ["git", "rg"]}}
    assert result["source_key"] == "exec:multi"
    assert result["activity"] == "command"
    assert "source" not in result["attrs"]  # detail stays on the call


def test_existing_attrs_are_preserved(database):
    with get_db() as db:
        _store(db, "r", "/v1/messages", ANTHROPIC_REQUEST, agent="claude_code")
        row = db.execute(BlockRecord.__table__.select().where(BlockRecord.block_type == "tool_call")).one()
        db.execute(BlockRecord.__table__.update().where(BlockRecord.id == row.id).values(
            attrs=json.dumps({"cache_control": {"type": "ephemeral"}, "custom": 1})))
        migrations._migrate_to_v9(db)
        call = next(b for b in crud.get_blocks(db, "r") if b["block_type"] == "tool_call")
    assert call["source_key"] == "bash:git"
    assert call["attrs"] == {"cache_control": {"type": "ephemeral"}, "custom": 1}  # no detail for one program


def test_documents_that_no_longer_match_leave_paths_empty_but_still_classify(database):
    tampered = json.loads(json.dumps(ANTHROPIC_REQUEST))
    with get_db() as db:
        _store(db, "r", "/v1/messages", ANTHROPIC_REQUEST, agent="claude_code")
        # The retained document drifted from what was analysed (e.g. an older adapter version).
        tampered["messages"].pop(0)
        db.execute(Request.__table__.update().where(Request.id == "r").values(
            canonical_request_body=json.dumps(tampered)))
        migrations._migrate_to_v9(db)
        request, blocks = _stored(db, "r")
        stats = db.info["v9_backfill"]
    assert request.purpose == "tool_continuation"
    assert all(b["json_path"] is None for b in blocks if b["direction"] == "input")
    assert all(b["source_key"] is not None for b in blocks)
    assert stats["path_mismatch"] == 1 and stats["paths_set"] == 0


def test_requests_without_retained_documents_get_classified_but_no_paths(database):
    with get_db() as db:
        _store(db, "r", "/v1/messages", ANTHROPIC_REQUEST, ANTHROPIC_RESPONSE, agent="claude_code",
               keep_documents=False)
        migrations._migrate_to_v9(db)
        request, blocks = _stored(db, "r")
    assert request.purpose == "tool_continuation"
    # Response blocks exist, so the response is known even though the document was purged.
    assert json.loads(request.purpose_detail)["response"] == {"kind": "final_text"}
    assert all(b["json_path"] is None and b["source_key"] for b in blocks)


def test_indeterminate_response_is_omitted_not_guessed(database):
    with get_db() as db:
        _store(db, "r", "/v1/messages", ANTHROPIC_REQUEST, None, agent="claude_code", keep_documents=False)
        migrations._migrate_to_v9(db)
        request, _ = _stored(db, "r")
    assert "response" not in json.loads(request.purpose_detail)


def test_requests_without_blocks_stay_untouched(database):
    with get_db() as db:
        crud.create_request(db, {"id": "empty", "timestamp": _TS, "provider": "test", "endpoint": "/v1/messages"})
        migrations._migrate_to_v9(db)
        request, _ = _stored(db, "empty")
        stats = db.info["v9_backfill"]
    assert (request.purpose, request.purpose_detail, request.classifier_version) == (None, None, None)
    assert stats["skipped_no_blocks"] == 1 and stats["classified"] == 0


def test_second_run_is_a_no_op_and_stale_versions_are_rederived(database):
    with get_db() as db:
        _store(db, "r1", "/v1/messages", ANTHROPIC_REQUEST, agent="claude_code")
        _store(db, "r2", "/v1/chat/completions", CHAT_REQUEST, classifier_version=0)
        migrations._migrate_to_v9(db)
        assert db.info["v9_backfill"]["classified"] == 2  # r2's version 0 is below the current one
        migrations._migrate_to_v9(db)
        again = db.info["v9_backfill"]
    assert again["classified"] == 0 and again["paths_set"] == 0 and again["source_keys"] == 0


def test_batching_covers_every_request(database, monkeypatch):
    monkeypatch.setattr(migrations, "_V9_BATCH_REQUESTS", 2)
    with get_db() as db:
        for index in range(7):
            _store(db, f"r{index}", "/api/chat", OLLAMA_REQUEST)
        migrations._migrate_to_v9(db)
        classified = db.execute(
            Request.__table__.select().where(Request.classifier_version == CLASSIFIER_VERSION)
        ).all()
        stats = db.info["v9_backfill"]
    assert len(classified) == 7 and stats["classified"] == 7


def test_progress_is_reported(database, monkeypatch):
    messages = []
    monkeypatch.setattr(migrations, "progress_reporter", messages.append)
    monkeypatch.setattr(migrations, "_V9_PROGRESS_EVERY", 2)
    monkeypatch.setattr(migrations, "_V9_BATCH_REQUESTS", 2)
    with get_db() as db:
        for index in range(5):
            _store(db, f"r{index}", "/api/chat", OLLAMA_REQUEST)
        migrations._migrate_to_v9(db)
    assert messages[0].startswith("v9: backfilling 5 requests")
    assert any("processed" in message for message in messages)
    assert messages[-1].startswith("v9: done.")


def test_unknown_endpoint_and_corrupt_document_do_not_abort_the_migration(database):
    with get_db() as db:
        _store(db, "bad-doc", "/v1/messages", ANTHROPIC_REQUEST, agent="claude_code")
        db.execute(Request.__table__.update().where(Request.id == "bad-doc").values(
            canonical_request_body="{not json"))
        _store(db, "good", "/api/chat", OLLAMA_REQUEST)
        migrations._migrate_to_v9(db)
        stats = db.info["v9_backfill"]
        good, _ = _stored(db, "good")
        bad, _ = _stored(db, "bad-doc")
    assert stats["path_failed"] == 1
    assert good.classifier_version == CLASSIFIER_VERSION and bad.classifier_version == CLASSIFIER_VERSION


def test_data_migration_runs_through_the_pending_flow(database):
    with get_db() as db:
        _store(db, "r", "/api/chat", OLLAMA_REQUEST)
        migrations.set_meta(db, "schema_version", "8")
        migrations.set_meta(db, "pending_data_migrations", json.dumps([9]))
        assert migrations.apply_data_migrations(db) == [9]
        assert db.get(Request, "r").classifier_version == CLASSIFIER_VERSION
        assert migrations.get_meta(db, "schema_version") == str(migrations.SCHEMA_VERSION)
