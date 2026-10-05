# Plan 2: Request purpose and extensible classification

Status: **specified in [wi0-data-foundation.md](wi0-data-foundation.md) (implement from that file; this document is background). Implemented in WI-0 (baseline only: `user_turn`, `tool_continuation`, `compaction`, `unknown`); `housekeeping`/agent detectors are not implemented. See `wi0-data-foundation.md` §17 for deviations from this document.** **Revised 2026-10-05 per `analysis-architecture.md` (read it first; it wins on conflicts).** Part of [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md) (decisions D1–D10).

> Naming: this was "C. Request purpose inference" in the brainstorm and briefly called "classification
> plan". It is deliberately **not** about `analysis/classifier.py` token *categories*; those stay as they are.

## Goal

Infer and show the **main purpose of each request** (user prompt, tool-result continuation,
housekeeping call, compaction, …) and give blocks a structured **source** (which tool / MCP server /
agent produced them), so that:

- the Requests list and conversation views can label and filter requests,
- the context tree (plan 5) can group by turn and by tool,
- future refinements (specific tools, MCP servers, subagents) need **no schema churn** (D7).

## Design

### Request purpose (persisted: it is a fact about the request itself)

New columns on `requests` (additive, via `db/database.py:_migrate()`; backfill via
`db/migrations.py` `_migrate_to_v9` and `SCHEMA_VERSION = 9`):

| Column | Type | Meaning |
|--------|------|---------|
| `purpose` | String, nullable | Coarse **stable** enum (see below). UI filters/colours rely on it. |
| `purpose_detail` | Text (JSON), nullable | Open-ended refinements; new keys need no migration. |
| `classifier_version` | Integer, nullable | Version of the derivation logic that produced the two fields; lets a later migration re-derive only stale rows. |

Initial `purpose` values (keep the set small; refine via `purpose_detail`):

- `user_turn` — last input message is a user message (text) → the user asked something.
- `tool_continuation` — last input message carries tool result(s) → agent loop step.
- `compaction` — request whose purpose is summarising/compacting the conversation.
- `housekeeping` — side calls not part of the main loop (title generation, classifiers, small-model helpers).
- `unknown` — could not be determined. Always allowed; never guess.

`purpose_detail` examples (all optional, all request-local):

```json
{"trailing_tool_results": ["Read", "Grep"],
 "response": {"kind": "tool_calls", "tool_calls": ["Edit"]},
 "agent_role_hint": "subagent"}
```

`response.kind` ∈ `tool_calls | final_text | mixed | empty`, derived from output blocks, so the UI can
render "→ Edit" or "→ final answer".

Heuristics live in a new `contextspy/analysis/purpose.py` (pure functions over `AnalyzedRequest` /
persisted blocks + `agent`, `model`, `endpoint`). Detecting `housekeeping`/`compaction` needs
agent-specific signals (system prompt shape, no tools, small model, known prompt markers); implement
Claude Code first, treat everything unrecognised as `user_turn`/`tool_continuation`/`unknown` by the
structural rule. **Verify real captured data before fixing heuristics** — inspect a few sessions in
`~/.contextspy/` and add fixtures.

Call the derivation from the capture path where blocks are classified (see `analysis/capture.py` /
`classify`) so new requests are labelled at insert; the migration backfills history **from retained
block rows** (block rows are never purged, so no raw bodies are needed).

### Turn grouping (derived at read time, NOT persisted)

An earlier idea was a persisted `turn_id`. **Rejected**: lineage/conversation membership is derived
and never persisted (see `db/session_lineage_service.py`), and a turn is relative to a conversation.
Instead:

- Persist only the request-local `purpose` (above).
- Compute turns in the lineage/analysis layer: a turn starts at a `user_turn` request and includes
  its `tool_continuation` descendants along the conversation until the next `user_turn`. Expose
  `turn_index` / `turn_start_request_id` on the lineage nodes next to `conversation_code`
  (`crud.annotated_lineage_nodes`), cached with the graph. Bump `ANALYSIS_VERSION` if graph output changes.
- Breaks in lineage (compaction, restarts) start a new turn run and are surfaced, not hidden (D5).

> **REVISION (D12/D13/D15):** block `source` is now **persisted** as `blocks.source_key` (see
> `analysis-architecture.md` §2.2) because Codex's generic `exec` tool needs argument parsing and args
> content is purged on archive. The section below describes the original read-time idea and is kept
> for the descriptor shape; implement persistence + `activity` mapping as in the architecture doc.
> Schema changes are part of the batched v9 migration, not an independent `_migrate_to_v9`.
> Housekeeping/compaction heuristics must be validated on captures from **every supported agent/provider** (Claude Code, Codex, Copilot, Ollama/llama.cpp/vLLM); the structural baseline must be provider-neutral (D16).

### Block source (originally: derived at read time)

`BlockRecord.tool_name` is already stored, so a block's *source* can be computed on read:

`contextspy/analysis/sources.py: describe_source(block_type, tool_name, agent) -> dict`, e.g.

```json
{"kind": "tool", "namespace": "mcp", "server": "github", "name": "create_issue"}
{"kind": "tool", "namespace": "builtin", "name": "Read"}
{"kind": "system"} / {"kind": "user"} / {"kind": "assistant"}
```

Parsing MCP names (`mcp__<server>__<tool>` for Claude Code; other agents may differ — **verify per
agent against captured data**) is the first refinement. Because nothing is stored, improvements
apply to old data immediately, and no migration is needed. Exposed through the existing block API
payload (`BlockRecord.to_dict` / `crud.get_blocks`) as `source`. If a later feature needs SQL-level
grouping by server, group in Python after fetching (hot spots already aggregate in Python) or
reconsider persisting then.

## API / UI

- Add `purpose`, `purpose_detail` to request payloads (`Request.to_dict`, list + detail) and
  `source` to block payloads. Add `?purpose=` filter to the requests list (see `tests/test_request_filters.py`).
- Frontend: label/chip for purpose in `RequestTable`, request cards in the conversation flow, and
  `RequestSummaryHeader`; icon/colour per purpose in a small lookup next to `lib/blockVisuals.ts`.
  Display only what the API returns (policy). Source shown in `BlockInspector`.

## Migration checklist (per memory/AGENTS rule)

- [ ] `db/models.py`: 3 new columns + `to_dict`.
- [ ] `db/database.py:_migrate()`: additive `ALTER TABLE` for each.
- [ ] `db/migrations.py`: `_migrate_to_v9` backfill (keyset batches like v8, idempotent via
      `classifier_version IS NULL OR < CURRENT`), register in `_DATA_MIGRATIONS`, `SCHEMA_VERSION = 9`.
- [ ] `tests/test_migrations.py` case for v8→v9.

## Tests

1. Structural rule: trailing user text → `user_turn`; trailing tool_result → `tool_continuation` for
   each adapter (Anthropic, OpenAI chat, Responses, Ollama) using existing fixtures in `tests/test_providers.py`.
2. `response.kind` from output blocks (tool calls vs final text vs mixed vs empty).
3. Claude Code housekeeping/compaction fixtures (real, anonymised captures).
4. `describe_source`: builtin tool, MCP `mcp__server__tool`, name with extra underscores, missing name.
5. Turn derivation: user_turn + continuations grouped; lineage break starts new turn; aux requests excluded.
6. Backfill idempotent; rows with older `classifier_version` re-derived; purged raw bodies irrelevant.
7. API filter `?purpose=`.

## Out of scope

Per-tool/MCP-specific purposes beyond `trailing_tool_results`/`source` naming; cost; cache.

## Open questions

- Exact housekeeping/compaction signals per agent (needs data inspection first).
- Should `subagent` be a purpose value or only `agent_role_hint`? Default: detail only, since a
  subagent's request looks like `user_turn`/`tool_continuation` locally; revisit with lineage data (`AUX`).
