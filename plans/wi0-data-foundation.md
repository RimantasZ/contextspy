# WI-0: Data foundation — migration v9, capture-time classification, `json_path`

Status: **implemented (all five slices) on 2026-10-05. Committed: slice 1 in `528f3db`, slices 2–5 in `88bd6e0`. Not released.**
Spec written 2026-10-05. Where the code differs from the text below, §17 (Implementation log) is authoritative.

| Slice (§13) | Status |
|-------------|--------|
| 1. Schema v9 | **done** |
| 2. `Block` fields, `insert_blocks`, `json_path` in all adapters | **done** |
| 3. `sources.py` / `purpose.py` / `activity.py` + capture integration | **done** |
| 4. Backfill (`_migrate_to_v9`) | **done**, dry-run on a copy of a real 6.6 GB database (see §17) |
| 5. API filter + UI surfaces + docs | **done** (UI covers Request detail only; see §17 for what is *not* surfaced) |

Verification at completion: `pytest` 467 passed; `cd ui && npm run check` (types, lint, 142 tests, knip, build) passed.
Not done: a manual check of the new UI in a running browser, and a run of `contextspy db-upgrade` on the real database.

Parent: [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md) (decisions D1–D16). Rationale and measurements:
[analysis-architecture.md](analysis-architecture.md) (it wins on conflicts). Refines: [request-purpose.md](request-purpose.md).

Audience: implementing agents. Everything here is verified against the code as of 2026-10-05; line numbers
drift, so re-find symbols by name. Follow `AGENTS.md` (analysis in Python; schema change ⇒ `_migrate()` + `migrations.py`).

## 0. Ground rules

- **Provider neutral (D16).** Baseline logic works for every adapter (Anthropic, OpenAI chat, OpenAI responses, Ollama;
  Copilot/llama.cpp/vLLM/OpenAI-compatible go through these by endpoint path — `get_adapter`). Agent-specific logic is
  an optional registered plug-in. Never justify a rule by the contents of any local DB. Test with synthetic fixtures.
- **Never guess.** Unknown ⇒ NULL / `unknown`. No heuristic may raise: classification failures must not break capture
  (wrap, log at debug/warning, store NULLs).
- **Tokens only (D1), no cache logic (D2).** Nothing here touches cost.
- **Capture path must stay fast**: classification is O(blocks) string work, no DB access, no tokenization.

## 1. Deliverables

1. Schema v9: new columns + indexes, `SCHEMA_VERSION = 9`, data migration `_migrate_to_v9` (backfill).
2. `Block` gets `json_path` and `source_key`; all four adapters set `json_path`.
3. New pure modules `analysis/sources.py`, `analysis/purpose.py`, `analysis/activity.py`.
4. Capture integration (writes purpose + source keys at insert).
5. API exposure (`purpose`, `purpose_detail`, `classifier_version`, block `source_key`/`activity`/`json_path`; `?purpose=` filter) and the minimal UI surfaces in §9.
6. Tests, docs (§11–§12).

Out of scope: archive (plan 3), occurrences/hot spots/tree/compare (plans 1, 4–6), retention-default change (D14, ships with archive),
`housekeeping`/`compaction` detection (no fixtures yet; enum values are reserved but **not emitted** in this WI — see §5.3).

## 2. Schema (v8 → v9)

`db/models.py` + `db/database.py:_migrate()` (additive `ALTER TABLE`, idempotent via the existing try/except pattern):

| Table | Column | SQL type | Notes |
|-------|--------|----------|-------|
| `requests` | `purpose` | `TEXT` NULL | enum string, §5 |
| `requests` | `purpose_detail` | `TEXT` NULL | JSON object, §5 |
| `requests` | `classifier_version` | `INTEGER` NULL | `CLASSIFIER_VERSION = 1` (constant in `analysis/purpose.py`) |
| `blocks` | `source_key` | `TEXT` NULL | grammar §4 |
| `blocks` | `json_path` | `TEXT` NULL | compact JSON array, §6 |
| `sessions` | `archived_at` | `DATETIME` NULL | unused until plan 3; added now to avoid a second migration (D15) |

Indexes (also in `_migrate()` with `CREATE INDEX IF NOT EXISTS`, and declared in `models.py`):
- `idx_blocks_source_key ON blocks (source_key)`
- `idx_requests_session_purpose ON requests (session_id, purpose)` (a bare index on a 5-value column is useless in SQLite; the filter is always combined with a session or a time sort)

`models.py` updates: columns on `Request`, `BlockRecord`, `Session`; `Request.to_dict` (+`purpose`, `purpose_detail` decoded dict|None,
`classifier_version`); `BlockRecord.to_dict` (+`source_key`, `activity` via `activity_for`, `json_path` decoded list|None);
`Session.to_dict` (+`archived_at` ISO|None; do **not** add a `status` yet).
`migrations.py`: `SCHEMA_VERSION = 9`, register `9: _migrate_to_v9` in `_DATA_MIGRATIONS`.
`tests/test_migrations.py::test_inspect_migration_state_is_read_only_for_legacy_database` currently asserts `pending == [2..8]` → `[2..9]`;
grep the test-suite for other hard-coded `8`/`[..8]` expectations.

Compatibility: DB at v8 with new code and v9 *not yet applied* (`db-upgrade` not run) must keep working: all new columns are nullable and every
reader tolerates NULL (UI shows nothing / generic labels).

## 3. Data types

`analysis/blocks.py`:
```python
@dataclass
class Block:
    ...
    source_key: str | None = None
    json_path: tuple[str | int, ...] | None = None   # path into the canonical JSON document of this block's direction
```
`Block.make(..., json_path=None)` kwarg passes through. `crud.insert_blocks` writes
`source_key=b.source_key`, `json_path=json.dumps(list(b.json_path), separators=(",", ":")) if b.json_path is not None else None`.

A light read-only view is used where no `Block` object exists (backfill, API):
```python
class BlockView(Protocol):   # satisfied by Block and by a small dataclass built from BlockRecord rows
    direction: str; block_type: str; message_index: int | None
    tool_name: str | None; tool_call_id: str | None; attrs: dict; content: str | None
    source_key: str | None
```

## 4. Block `source_key` — `analysis/sources.py`

Purpose: a compact, indexable, provider-neutral identity of *what produced this block* (D12). Persisted because tool-call arguments
are purged by retention/archive.

### 4.1 Grammar
`<namespace>:<name>` lowercase namespace; or one of the bare keys `system`, `user`, `assistant`, `reasoning`, `other`.

| Key | Meaning |
|-----|---------|
| `system`, `user`, `assistant`, `reasoning` | by `block_type` (`system_prompt`, `user_message`, `assistant_message`/`assistant_prefill`, `thinking`) |
| `tool:<name>` | generic baseline for tool definition/call/result with `tool_name`; `tool:unknown` if a call/result has no resolvable name |
| `mcp:<server>/<tool>` | tool name of the form `mcp__<server>__<tool>` (split on the first `__` after `mcp__`; server may not contain `__`) |
| `<wrapper>:<program>` | produced by a registered parser, e.g. `exec:rg`; `<wrapper>:multi`, `<wrapper>:js` (unparseable snippet) |
| `other` | anything else (`block_type == other`, etc.) |

Rules:
- Tool **definition**, **call** and **result** of the same tool share the same key. A result gets the key of its call, found by
  `tool_call_id` among the request's tool-call blocks (input and output); fallback to `tool_name`, else `tool:unknown`.
- `source_key` is **computed once at capture** and never recomputed on read.
- The namespace is always lowercase; `<name>` keeps the provider's casing (`tool:Read`). Activity lookup is case-insensitive on `<name>`.

### 4.2 API
```python
@dataclass(frozen=True)
class SourceInfo:
    key: str
    detail: dict | None = None     # JSON-serialisable, e.g. {"calls": ["rg", "sed"]}; stored in block attrs["source"]

def baseline_source(block: BlockView) -> SourceInfo: ...
def resolve_sources(blocks: Sequence[BlockView], *, agent: str | None) -> list[SourceInfo]:
    """Pure: one SourceInfo per input block, same order; never raises (falls back to baseline per block).
    The caller applies the result: capture sets block.source_key and block.attrs["source"] (detail);
    the backfill writes the same values to the DB rows. Keeps one implementation for both paths."""

# Plug-in registry
SourceParser = Callable[[BlockView, str | None], SourceInfo | None]
def register_source_parser(*, agents: frozenset[str] | None, tool_names: frozenset[str], parser: SourceParser) -> None
```
`resolve_sources`: for each block, first matching registered parser (agent matches or `agents is None`, `tool_name` matches, block is a tool
*call* with content) → its `SourceInfo`; `None` result or exception → baseline. Then a second pass assigns results/definitions via the call map.
Only **tool-call** blocks are parsed by plug-ins (they hold the arguments); definitions get `tool:<name>`, results inherit.

### 4.3 Parsers shipped in this WI: Codex `exec`/`js` and `Bash` (decision 2026-10-05)
Register for `agents={"codex"}`, `tool_names={"exec", "js"}`. Input is the tool-call block content: a **JavaScript snippet**, not JSON, e.g.

```js
const r = await tools.exec_command({cmd:"sed -n '290,335p' src/app.ts; sed -n '480,565p' src/app.ts", workdir:"/repo", max_output_tokens:3300}); text(r.output);
const patch = "*** Begin Patch\n*** Update File: x.py\n..."; await tools.apply_patch(patch);
const r = await Promise.allSettled([ tools.exec_command({cmd:"git status --short"}), tools.exec_command({cmd:"rg -n foo src"}) ]);
```
Algorithm (heuristic by design; must never raise):
1. Find `tools.<fn>(` calls via regex. For `exec_command`, extract the `cmd` string literal (single/double/backtick quoted; handle `\"` and `\\` escapes; give up on template expressions).
2. For `apply_patch` (call or `*** Begin Patch` literal) the program is `apply_patch`.
3. For a shell command string: take the first command segment (split on `&&`, `;`, `||`, `|`, newline), skip `cd <dir>` segments, leading `VAR=value` assignments and `sudo`/`env`/`time`; program = basename of the first word.
4. One distinct program ⇒ `exec:<program>`. Several distinct ⇒ `exec:multi` with `detail={"calls":[...]}` (order kept, deduped, max 20). Zero parsed calls ⇒ `exec:js`.
**Privacy rule:** `source_key` and `attrs["source"]` store program names only — never command arguments, paths, patch bodies or any other part of the snippet (they persist after archive).
Fixtures: synthetic, anonymised (no real paths/users) covering each shape above plus: no `cmd`, nested quotes, `cmd:` built from a variable, empty snippet, very large snippet (≤ ~1 MB scanned; cap work).
**Shared helper** `shell_program(cmd: str) -> str | None` (step 3 above) is used by both parsers; unit-test it directly.

### 4.4 `Bash` parser (JSON-argument shell tools)
Register for `agents=None`, `tool_names={"Bash", "bash"}`. Tool-call content is JSON arguments (e.g. `{"command": "git diff --stat | head", "description": "...", "run_in_background": false}`; accept `command` or `cmd`).
1. `json.loads` the content; not a dict, missing/non-string command, or invalid JSON ⇒ `None` (baseline `tool:Bash`).
2. `program = shell_program(command)`; key `bash:<program>` (namespace is the lowercased tool name); no program ⇒ `None`.
3. Same privacy rule: store only the program name. A single command string yields a single program (pipelines/`&&` chains use the first meaningful segment, same as §4.3 step 3); `detail=None`.
Fixtures (synthetic): plain command, `cd dir && cmd`, `VAR=1 cmd`, pipeline, `sudo`, quoted/escaped, heredoc, empty command, missing key, non-JSON content, very long command. Real captures of Claude Code `Bash` calls (local DB, read-only) may inspire fixtures but must be anonymised and may not set rules by frequency.

**Do not add further parsers** in this WI; other agents' wrappers are future work specified from their captures.

## 5. Request purpose — `analysis/purpose.py`

### 5.1 Columns
`purpose`: `user_turn | tool_continuation | compaction | housekeeping | unknown`.
In this WI only `user_turn`, `tool_continuation`, `unknown` are **emitted**; `compaction`/`housekeeping` are reserved (UI/API must tolerate them; filter accepts them).
`purpose_detail` JSON object (all keys optional, omitted when unknown):

```json
{
  "has_user_text": false,
  "trailing_tool_results": ["Read", "Grep"],
  "response": {"kind": "tool_calls", "tool_calls": ["Edit"]}
}
```
`classifier_version`: `CLASSIFIER_VERSION = 1`.

### 5.2 Baseline rules (provider-neutral, structural)
Input is the request's input blocks (`Block` or `BlockView`), output blocks, and `has_response: bool`.
1. `last_idx` = maximum non-negative `message_index` among input blocks (blocks with `None`/negative index are structural: system, tool definitions). No such index ⇒ `unknown`.
2. `tail` = input blocks with `message_index == last_idx`.
3. If any block in `tail` is `tool_result` ⇒ `tool_continuation`. `trailing_tool_results` = unique tool names of **all** `tool_result` blocks in the maximal trailing run of messages that contain tool results (so parallel results spread over several messages are all counted), resolved via the call map (`tool_call_id` → name; else the block's `tool_name`; else skipped). `has_user_text` = tail also has a non-prefill `user_message`.
4. Else if `tail` has a non-prefill `user_message` ⇒ `user_turn`.
5. Else (assistant prefill only, assistant-last, anything else) ⇒ `unknown`.
6. `response`: only if `has_response`. `kind`: `tool_calls` (output has `tool_call`, no non-empty assistant text), `mixed` (both), `final_text` (assistant text, no calls), `empty` (neither: thinking only/nothing). `tool_calls`: unique tool names/`source_key`s of output tool-call blocks (max 20).
Edge: Anthropic puts `tool_result` and user text in one user message ⇒ `tool_continuation`, `has_user_text=true`.

### 5.3 Agent plug-ins (registry only)
```python
PurposeDetector = Callable[[PurposeInputs], PurposeResult | None]
def register_purpose_detector(*, agents: frozenset[str] | None, detector: PurposeDetector) -> None
```
Detectors run before the baseline and may return `compaction`/`housekeeping`. **No detector ships in this WI** (needs captures; future WI, see §13).

### 5.4 Entry point
```python
@dataclass(frozen=True)
class RequestClassification:
    purpose: str | None
    purpose_detail: dict | None
    classifier_version: int
    def to_db_fields(self) -> dict      # {"purpose":..., "purpose_detail": json|None, "classifier_version": ...}

def classify_request(analyzed: AnalyzedRequest, *, agent: str | None, has_response: bool) -> RequestClassification
```
`classify_request` **first calls `resolve_sources`** on input+output blocks and applies the result to the `Block` objects (`source_key`, `attrs["source"]`), then derives purpose; wrapped so any exception ⇒ `RequestClassification(None, None, CLASSIFIER_VERSION)` after `logger.warning`, with blocks' `source_key` left as set so far.

## 6. `json_path` — adapters

### 6.1 Semantics
- Typed path array into the **canonical JSON document of the block's own direction**: input blocks → `requests.canonical_request_body`; output blocks → `canonical_response_body`. Segments are `str` (object key) or `int` (array index).
- The path points at the **smallest JSON node the block's content derives from**: a content-part **object** (not its `.text` leaf) when the block comes from a part; the **string value** when the content is a plain string; the **enclosing container** when several leaves are joined into one block (e.g. Anthropic `system` given as a list that is flattened). Never infer by searching text.
- `None` when no honest location exists (e.g. synthetic blocks such as a reasoning block added by `reconcile_thinking` for provider-reported tokens with no output text).
- For WS/delta transports the canonical (reconstructed) body is the target; this is automatic because adapters parse the canonical document (`analyze_invocation`).

### 6.2 Per-adapter targets (verify against the code; adjust only if a source shape differs)
Anthropic (`anthropic.py`): `system` str → `["system"]`; `system` list → `["system"]`; tool def → `["tools", i]`; message string content → `["messages", i, "content"]`; content part → `["messages", i, "content", j]` (text, tool_use, tool_result, thinking, other); response part → `["content", j]`.
OpenAI chat (`openai_chat.py`): `["messages", i, "content"]` (string) or `["messages", i, "content", j]` (part); assistant `tool_calls` → `["messages", i, "tool_calls", k]`; `tools` → `["tools", i]`; response → `["choices", c, "message", "content"]`, `[..., "tool_calls", k]`, reasoning/`reasoning_content` → `["choices", c, "message", "reasoning_content"]` (use the actual key encountered).
OpenAI responses (`openai_responses.py`): `instructions` → `["instructions"]`; string `input` → `["input"]`; item → `["input", i]`; message content part → `["input", i, "content", j]`; tools → `["tools", i]` (namespaced child tools: `["tools", i, "tools", j]`; the parent-token block, `_tool_definition_blocks` ~l.92, gets the parent path); output item → `["output", i]`, output message part → `["output", i, "content", j]`.
Ollama (`ollama.py`): `["messages", i]` per message (string content) / `["message", "content"]`, `["message", "thinking"]` for responses; `/api/generate` shapes: set only if the adapter already parses them, otherwise `None`.

Implementation: thread the path through each `Block.make(...)` call site (~50 across the four adapters) via the new `json_path=` kwarg; keep call sites readable (build the path tuple next to the loop index). Do **not** change block ordering, hashing, tokens or categories — a regression test must prove blocks are identical except for `json_path`.

## 7. Capture integration (`proxy/addon.py:_save_request`, `proxy/capture_writer.py`, `db/crud.py`)

1. In `_save_request`, right after `breakdown = classify(analyzed)` call `classification = classify_request(analyzed, agent=agent, has_response=bool(canonical and canonical.response))`; when `analyzed is None` skip classification entirely: `purpose`, `purpose_detail` and `classifier_version` stay NULL so unparsed rows are not stamped as classified.
2. `data.update(classification.to_db_fields())` next to `data.update(breakdown.to_db_fields())`.
3. `CaptureEnvelope.blocks` already carries the same `Block` objects (mutated in place by `resolve_sources`), so `crud.insert_blocks` persists `source_key`/`json_path` with no envelope change. Add the two fields to the `BlockRecord(...)` constructor there.
4. Older migrations (`_migrate_to_v2/v3/v5/v7`) call `classify` + `insert_blocks`; they need **no change** (new fields default to NULL; v9 backfills afterwards). Do not call `classify_request` from them.
5. `crud.create_request(db, data)` takes the dict → new `Request` columns flow through if present in `data`.

## 8. Backfill — `_migrate_to_v9` (`db/migrations.py`)

Follow `_migrate_to_v8` conventions: keyset batches, idempotent, bounded memory, `logger.info` progress, flush per batch; apply through the normal pending-migration flow (`db-upgrade`, with backup; do not auto-run at server start beyond the existing mechanism).

Pass over requests ordered by `id` (keyset), in batches of ~100 requests, selecting `Request.id, agent, endpoint, canonical_request_body IS NOT NULL, canonical_response_body IS NOT NULL, classifier_version`. For each batch load all its `BlockRecord` rows (one query, `request_id IN (...)`).

**A. Classification** (when `classifier_version IS NULL OR < CLASSIFIER_VERSION`):
- Build `BlockView`s from rows. For `source_key`, tool-call blocks of registered parsers need content: left-join `block_contents` on `content_hash` for tool-call rows only; if content is purged the parser is skipped ⇒ baseline key (`tool:exec`), a known and accepted loss (retained content is a minority of history).
- Call the same pure `resolve_sources` over the views, write `source_key` (+ `attrs["source"]` detail merged into existing attrs JSON, never dropping keys), compute purpose with the same `classify_request` core (shared function operating on `BlockView`s — implement the logic once over `BlockView`, and have `Block` satisfy the protocol), update `requests.purpose/purpose_detail/classifier_version`.
- `has_response`: true when the request has output block rows or `canonical_response_body` retained or `invocation_outcome` indicates a response; if indeterminate, omit `response` from the detail (do not guess).
- Requests with **no block rows** (unparsed) are left with NULLs and **not** stamped.

**B. `json_path`** (only when `canonical_request_body`/`canonical_response_body` is retained and the request has blocks with NULL `json_path`):
- `get_adapter(endpoint)`; parse the canonical document through the adapter exactly as capture does (`analyze_invocation`/`CanonicalJsonDocument.from_text`).
- Per direction: stored rows ordered by `position`; parsed blocks in order. **Unambiguous-match rule**: identical count **and** for every index equal `block_type` and equal `content_hash` (both None allowed) ⇒ copy `json_path`. Any mismatch ⇒ skip that direction entirely (leave NULL). Never match by content search.
- Adapter exceptions ⇒ skip the request, log at debug.

**Execution policy (decided 2026-10-05):** the backfill stays an explicit `contextspy db-upgrade` step (backup first). Startup already refuses to run while data migrations are pending (`cli.py:_abort_if_migrations_pending`), so there is no silent startup cost; do **not** auto-run the backfill in server start. `db-upgrade` must print progress (requests done / total, phase A vs B, elapsed).
**Expected cost** (measured on a ~6.9k-request, 1.35M-block DB, see below): phase B re-parse ≈ 56 ms per retained request (24 MB / 40 requests sampled ⇒ ≈ 11 MB/s, dominated by tokenizing each block), i.e. ≈ 2 minutes for ~1.9k retained requests; phase A is a streaming pass over block rows (~1.35M updates in batches) estimated at tens of seconds to a few minutes (**not yet measured end-to-end — measure on a copy before release**). Order of magnitude: **a few minutes** for a DB of this size, and it scales linearly with retained canonical-body volume and block-row count. If phase B proves too slow on big DBs, add a `--skip-json-path` option (blocks keep NULL; no data loss) rather than parallelising.
Idempotency: A keyed on `classifier_version`; B only touches NULL `json_path`. Re-running performs no wasted writes except re-parsing requests whose retained bodies cannot be matched (acceptable; cheap).
Performance expectation: tens of thousands of requests / millions of block rows must complete in minutes without loading more than one batch in memory; log every ~1000 requests. Add a test that proves batching (e.g. > batch-size requests).

## 9. API and UI surfaces (minimal, display only — D16/policy)

Backend:
- `Request.to_dict` and `BlockRecord.to_dict` additions (§2) flow into `GET /requests`, `GET /requests/{id}`, `GET /requests/{id}/blocks` (via `crud.get_blocks`) and the WebSocket `new_request` payload (`include_raw=False` path) — verify each, plus any hand-built dicts for dashboard/conversation request cards (`grep` for `"tokens_total_input"` builders in `crud.py`/`routers`) and add `purpose`/`purpose_detail` there only where cheap.
- `GET /requests?purpose=` (router `Query(default=None, pattern="^(user_turn|tool_continuation|compaction|housekeeping|unknown)$")`; `crud.list_requests(purpose=...)`; `unknown` also matches NULL like the existing `agent == "unknown"` convention). Extend `tests/test_request_filters.py`.
Frontend (`ui/src/api/client.ts` + minimal components; no analysis in TS):
- Types: `Request` (+`purpose`, `purpose_detail`, `classifier_version`), `RequestBlock` (+`source_key: string | null`, `activity: string | null`, `json_path: (string | number)[] | null`).
- `RequestSummaryHeader`: a small purpose chip (label map in `lib/`, e.g. `user_turn → "User turn"`, `tool_continuation → "Tool continuation"`, plus detail text "→ Edit"/"Read, Grep" from `purpose_detail`), hidden when `purpose` is null.
- `BlockInspector`: rows "Source" (`source_key`), "Activity", and "Raw JSON location" (shows the path as `messages[3].content[1]` when present, nothing when null).
- Update/extend `RequestDetail.test.tsx`, `BlockInspector.test.tsx`. Run `cd ui && npm test` and `make ui`.

## 10. Activity vocabulary — `analysis/activity.py` (derived at read, not stored)
`activity_for(source_key: str | None) -> str | None` — pure table, refinable without migration (D12). Bare keys `system|user|assistant|reasoning` ⇒ `None`.

| Source | Activity |
|--------|----------|
| `mcp:*` | `mcp` |
| `<wrapper>:<program>` with program in `rg grep ag ack find fd locate` | `search` |
| … `cat sed head tail less bat ls tree wc stat file nl` | `read` |
| … `git gh hg svn` | `vcs` |
| … `pytest jest vitest tox` | `test` |
| … `apply_patch patch` | `edit` |
| … `exec:multi`, other programs, `exec:js` | `command` |
| `tool:<name>` name (case-insensitive) in `read read_file view cat` | `read` |
| … `grep glob search find ripgrep` | `search` |
| … `edit write multiedit notebookedit str_replace_editor apply_patch` | `edit` |
| … `bash shell exec run terminal` | `command` |
| … `webfetch websearch fetch browser` | `web` |
| … `task agent spawn_agent send_message wait_agent` | `orchestration` |
| anything else | `other` |

The vocabulary (`read, search, edit, vcs, test, command, web, orchestration, mcp, other`) extends the user-approved list with `command`, `web`, `mcp`; expected to evolve. Keep the table in one module with a unit test per row.

## 11. Tests (pytest unless stated)

New: `tests/test_sources.py`, `tests/test_purpose.py`, `tests/test_activity.py`, `tests/test_json_path.py`, additions to `tests/test_migrations.py`, `tests/test_request_filters.py`, `tests/test_providers.py` (regression), frontend tests.
1. **Sources**: `shell_program` table-driven cases; Bash parser fixtures (§4.4); baseline keys for every block type; MCP split (`mcp__github__create_issue`, server with single underscores, malformed); tool def/call/result share key; result falls back to name/`tool:unknown`; parser exceptions ⇒ baseline; Codex `exec` fixtures (§4.3 incl. multi/js/apply_patch/escapes/huge); parser only for agent `codex`.
2. **Purpose**: per adapter synthetic request ⇒ `user_turn`, `tool_continuation` (Anthropic mixed result+text; OpenAI chat parallel tool messages; Responses `function_call_output`), assistant-last ⇒ `unknown`, empty input ⇒ `unknown`; `trailing_tool_results` names/dedupe; `response.kind` for each case; exception safety; registry detector overrides baseline.
3. **json_path**: for each adapter and each block type, build a canonical fixture, parse, and assert (a) exact expected path for each block, (b) every non-None path **resolves** in the document (`resolve(doc, path)` helper) and the resolved node contains the block's source text, (c) explicit allow-list of blocks with `None`. Regression: blocks (type/category/hash/tokens/order) identical to pre-change output (compare to existing `test_providers` fixtures).
4. **Capture**: end-to-end through `_save_request`/`persist_capture` (existing test style): row has `purpose`, `classifier_version=1`, blocks have `source_key`/`json_path`; unparsed request has NULLs; classification exception does not prevent persistence.
5. **Migration**: `_migrate()` adds columns+indexes idempotently on a v8-shaped DB; `_migrate_to_v9` backfills source keys/purpose/json_path (matching and mismatching/ambiguous cases, purged content, batch boundaries, idempotent second run, existing `attrs` keys preserved); `[2..9]` pending expectation; v8 DB + new code without applying v9 still serves requests/blocks (NULL tolerance).
6. **API**: `purpose` filter incl. `unknown`⇒NULL; payload fields on request list/detail/blocks; WS payload.
7. **Frontend**: chip render/hidden; inspector rows.

## 12. Docs and housekeeping
- `SPEC.md`, `docs/development.md` (schema v9, classifier version, how to add a source parser/purpose detector/activity row), `docs/changelog.md`, and `AGENTS.md` architecture list (mention `sources.py`, `purpose.py`, `activity.py`) in the same change.
- Update `plans/request-purpose.md` status to "specified in WI-0".
- Run: `pytest`, `cd ui && npm test`, `make ui`; manual: capture one request per adapter (or replay fixtures) and confirm columns; run `contextspy db-upgrade` on a copy of a real DB and check timing/log output.

## 13. Suggested slicing (each independently mergeable; keep the suite green)
1. Schema v9 (models, `_migrate`, indexes, stub `_migrate_to_v9`, `SCHEMA_VERSION=9`, test updates).
2. `Block` fields + `insert_blocks` + `json_path` in all adapters + tests.
3. `sources.py`, `purpose.py`, `activity.py` + capture integration + tests.
4. Backfill implementation + tests.
5. API/UI surfaces + docs.

## 14. Follow-ups (not in this WI)
- Housekeeping/compaction detectors per agent, and more source parsers (other agents' wrappers) — **after** captures from Claude Code, Copilot, Ollama, llama.cpp, vLLM exist; fixtures may be derived from a local DB read-only, but rules must be justified by wire format/agent behaviour, not frequency.
- Retention default `0` and archive (plan 3). Info panel "present in" (plan 1) consumes `source_key`/`activity`.

## 15. Acceptance criteria
- New requests of every adapter persist `purpose`/`purpose_detail`/`classifier_version`, block `source_key`, and `json_path` with resolvable paths; unparsed requests stay NULL.
- `db-upgrade` from v8 completes idempotently with a backup, backfilling per §8; no block ordering/hash/token changes anywhere.
- API and UI show purpose, source, activity and raw-JSON location when present and nothing when absent.
- All suites pass; `make ui` builds; docs updated.

## 16. Risks / notes
- Adapter edits touch ~50 call sites: mitigate with the regression test in §11.3.
- Backfill duration on large DBs: batch + progress logging; measure on a copy before release.
- `exec` parsing is heuristic: the NULL/`exec:js`/baseline fallbacks are deliberate, and wrong keys must be impossible to cause data loss (they only affect labels).
- `evidence_revision` (lineage cache key) does not include these columns; they do not affect lineage.

## 17. Implementation log

### Slice 1 — schema v9 (done 2026-10-05)
Implemented as specified in §2, with these specifics (verify in code before relying on them):
- `db/models.py`: `Request.purpose/purpose_detail/classifier_version`, `BlockRecord.source_key/json_path`, `Session.archived_at`;
  indexes `idx_blocks_source_key`, `idx_requests_session_purpose (session_id, purpose)`.
  `Request.to_dict` adds `purpose`, `purpose_detail` (decoded dict|None), `classifier_version`;
  `BlockRecord.to_dict` adds `source_key`, `json_path` (decoded list|None); `Session.to_dict` adds `archived_at` (no `status` yet).
  **Not yet added:** `BlockRecord.to_dict["activity"]` — arrives with `analysis/activity.py` in slice 3.
  Consequence: §9 backend payload work for these fields is already done; remaining in slice 5 are the `?purpose=` filter, any hand-built request dicts (dashboard/conversation cards) and the UI.
- `db/database.py:_migrate()`: six `ALTER TABLE … ADD COLUMN` entries (idempotent via the existing try/except) and two `CREATE INDEX IF NOT EXISTS`.
- `db/migrations.py`: `SCHEMA_VERSION = 9`; `_migrate_to_v9` registered as a **logging no-op** (replaced in slice 4).
- Tests: new `tests/test_schema_v9.py` (v8-shaped DB upgrade + idempotency, NULL tolerance of pre-upgrade rows, round-trip of v9 values through `to_dict`, registration). Updated hard-coded expectations in `tests/test_migrations.py` (`pending == [2..9]`, backup name `v1_to_v9`) and `tests/test_volatile_header.py` (`SCHEMA_VERSION >= 8`).
- Full suite at the time: 325 passed. (The real-database dry run was done with slice 4; see below.)
- Operational effect to remember: any database below v9 now has pending migration 9, so `contextspy start` refuses to run until `contextspy db-upgrade` is executed (existing gating behaviour, `cli.py:_abort_if_migrations_pending`).

### Slices 2–5 (done 2026-10-05) — what exists, and how it differs from the spec above

**Slice 2** — `Block.json_path`/`Block.source_key` and `Block.make(json_path=)`; `crud.insert_blocks` persists both;
all four adapters set `json_path` at every `Block.make` call site. Block order, hashes, tokens and categories are unchanged
(the whole pre-existing suite passes untouched). `tests/test_json_path.py` asserts exact paths for every block type and that
every path resolves and holds the block's text. Differences from §6.2: Ollama request paths are `["messages", i, "content"]`
(not `["messages", i]`); a bare-string Responses `input` has path `["input"]`; a single-object `input` has `["input", "content"]`;
an Anthropic string response `content` has path `["content"]`; Chat uses the choice's list position, not its `index` field.
The block synthesised by `reconcile_thinking` for provider-reported reasoning has no path (by design).

**Slice 3** — new `analysis/sources.py`, `analysis/purpose.py`, `analysis/activity.py`; wired into `proxy/addon.py:_save_request`.
Differences from §4–§5:
- `resolve_sources` is pure (returns `list[SourceInfo]`); `classify_request` applies the result. It returns **`None`** (not a
  classification) when the analysis produced no blocks, so unparsed requests stay NULL, including when an adapter failed.
- **Tool definitions keep the baseline key (`tool:Bash`)** while calls/results carry the parsed key (`bash:git`); §4.1's
  "definition, call and result share one key" only holds without a parser.
- **Both parsers shipped**: Codex `exec`/`js` (§4.3) and `Bash`/`bash` (§4.4). Shared `shell_program` also skips
  `sudo -u x`, `env -u X`, `nice -n`, full-path wrappers and `(cd x && cmd)` forms.
- `purpose_detail.response.tool_calls` lists **tool names** (not source keys).
- **Baseline purpose rule was extended after testing on real data** (not in §5.2): messages consisting only of
  `system_prompt` or `thinking` blocks are skipped when finding the last conversational message (providers append
  system/developer instruction messages and reasoning items after the real last turn; without this ~460 Claude Code requests were
  `unknown`), and a tail containing a `provider_item_type == "compaction_trigger"` item yields `compaction`. This is a
  wire-format signal the OpenAI Responses adapter already models, not an agent heuristic, but it is a judgement call: review it.
  `housekeeping` is still reserved and never emitted.
- `BlockSnapshot` carries `content_hash` so "assistant produced text" is known for stored rows whose content was not loaded.
- `activity_for` vocabulary: read, search, edit, vcs, test, command, web, orchestration, mcp, other. `BlockRecord.to_dict` now
  returns `activity`.

**Slice 4** — `_migrate_to_v9` as specified in §8: keyset batches of 100 requests, bulk `UPDATE`s by primary key, progress via the log and
`migrations.progress_reporter` (set by `contextspy db-upgrade`), summary in `db.info["v9_backfill"]`. Only fills NULL `json_path`
values. **No `--skip-json-path` option** (the spec's fallback was not needed).
Dry run on a copy of the author's database (6,949 requests, 1.37M blocks, schema v8): **174 s**, 1,372,152 source keys, 458,964 paths,
0 mismatches, 0 re-parse failures (a second run with classification reset took 38 s because paths already existed).

**Slice 5** — `GET /api/requests?purpose=` (+ `crud.list_requests(purpose=)`; `unknown` also matches NULL); `purpose` added to the
conversation flow items (`crud._flow_item`; the UI does not use it yet); UI: types, `lib/purpose.ts`, `lib/jsonPath.ts`,
purpose chip + one-line summary in `RequestSummaryHeader`, Source / Activity / Commands-in-call / Raw JSON location rows in
`BlockInspector`. Docs: `SPEC.md`, `docs/development.md`, `docs/changelog.md`, `AGENTS.md`.
**Not surfaced in the UI:** purpose chips in the request list (`RequestTable`) or conversation cards, a purpose filter control,
and jumping from the JSON location to the raw JSON viewer (that is the future context-tree plan).

### Findings from the dry run (read before releasing)
1. **The live database already has a `blocks.json_path` column** that this work did not add (live schema is still v8, with none of the other
   v9 columns). 6,794 blocks from 2026-08-28 (7 requests) hold values in a *different convention*: leaf paths such as
   `["input", 55, "input"]` or `["input", 259, "output"]`, where this implementation records the item (`["input", 259]`). They come from an
   earlier prototype, probably in the `contextspy-gpt` worktree. The backfill never overwrites existing values, so those rows keep the
   leaf form (still valid paths, just more specific); new and backfilled rows use the item/part form. Decide whether to normalise them.
2. Real-data purpose distribution after the final rule: Claude Code 366 `tool_continuation` / 215 `user_turn` / 4 `unknown`;
   Codex 5,612 / 295 / 405 `unknown` / 40 `compaction`; 12 requests without blocks stay NULL.
3. `tool:exec` remains the key for Codex calls whose content was purged; only retained tool-call text can be parsed.

