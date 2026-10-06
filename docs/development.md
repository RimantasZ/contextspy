# Development

## Backend

```bash
git clone https://github.com/RimantasZ/contextspy.git
cd contextspy
uv venv
uv pip install -e ".[dev]"
uvicorn contextspy.api.main:create_app --factory --reload --port 5173
```

## Tests

Backend tests cover adapters/classification, invocation normalization, WebSocket assembly,
database migrations, and request filtering. Frontend tests cover request tables/filters, the
request workbench and block maps, content search, theme behavior, and tool-treemap transformation.
Run them with:

```bash
pytest
# or a single test:
pytest tests/test_providers.py::test_name

cd ui
npm test

# Run the complete UI validation suite (types, lint, tests, unused code, build)
npm run check
```

When modifying `analysis/adapters/`, `analysis/classifier.py`, normalization, capture, database, or
proxy protocol code, run the backend suite. Run the frontend suite and build after UI changes.

## Frontend

```bash
cd ui
npm install
npm run dev   # Vite dev server on :5174, proxies /api (including /api/ws) to :5173
```

The built UI is embedded in the Python package at `contextspy/_web/`. Rebuild after
changing anything under `ui/src/`:

```bash
cd ui && npm run build   # outputs to contextspy/_web/
```

---

## Architecture

### Cloud mode

```
coding agent → HTTPS_PROXY → mitmproxy (port 8888)
                                  │
                            ContextSpyAddon (intercepts here)
                              → identify one provider invocation
                              → normalize provider state into canonical JSON
                              → parse canonical request/response JSON only
                              → classify tokens into 8 categories
                              → write to SQLite
                              → broadcast via WebSocket
                                  │ TLS terminate + forward
                              cloud LLM API
```

### Local mode

```
client (base_url=:8889) → mitmproxy reverse proxy (port 8889)
                                  │ plain HTTP forward
                            llama-server / Ollama / vLLM (port 8080…)
                                  │
                            ContextSpyAddon (per-target provider_override;
                                              commonly "openai")
                              → capture/reconstruct, parse, classify, count tokens
                              → write to SQLite
                              → broadcast via WebSocket
```

Both modes share the same FastAPI web server (port 5173), SQLite database, and dashboard.

---

## Data storage

All data is stored in `~/.contextspy/`:

| Path | Description |
|------|-------------|
| `~/.contextspy/contextspy.db` | SQLite database — all requests and sessions |
| `~/.contextspy/config.toml` | Configuration file (auto-created on first run) |

File-backed databases use SQLite WAL mode. While ContextSpy runs, SQLite may also create
`contextspy.db-wal` and `contextspy.db-shm`; these are part of the live database and must not be
deleted or copied separately. WAL lets dashboard reads overlap capture commits, while bounded
retries handle transient writer contention. A capture that still cannot be saved logs
`capture_not_saved` with its request ID; the provider request itself may have succeeded.
For a backup while the database is live, use SQLite's online backup API. A plain file copy is
safe only after all ContextSpy processes and other database connections have stopped and the
database has checkpointed. The `db-upgrade` command already uses an online backup.
`db-backup` uses the same SQLite snapshot implementation on demand. Both publish a verified,
standalone `.back` file in rollback-journal mode, so no sidecar is required to restore it.
Offline `db-restore` stages and verifies that file, checkpoints/converts the stopped current
database to a standalone pre-restore rollback file, then switches the active path. It refuses
to proceed if the app-level database lock or SQLite sidecars indicate another user of the DB.
If WAL must be rolled back for an older ContextSpy build, first stop all processes and make a
recoverable backup. Then open the database with SQLite, run `PRAGMA wal_checkpoint(TRUNCATE);`
and `PRAGMA journal_mode=DELETE;`, and confirm that the latter returns `delete` before starting
the older build. The current build enables WAL again on its next start; never remove sidecar
files manually as a rollback method.

Observed request payloads, canonical request/response payloads, normalized SSE/NDJSON/WebSocket
event logs, plus the content-addressed `block_contents` table (see below), are removed explicitly by
**archiving a session** (`db/session_archive.py`; `POST /api/sessions/{id}/archive`, `contextspy session archive`).
The older time-based purge is configured via `[retention]` in `config.toml` (`raw_body_days`,
`block_content_days`) and is **off by default (`0`)**; explicit values are honoured and `startup_vacuum` logs a notice
when it is enabled. It only runs once, at server startup — there is no background timer, so a `contextspy`
process left running won't purge again until restarted.

Archive details worth knowing when changing `session_archive.py`: it only runs for ended sessions and is repeatable; the
content cleanup is chunked (≤ 500 hashes) and every chunk takes the write lock first (`_take_write_lock`) before measuring and
deleting with the "not referenced outside this session" condition inside the `DELETE`, because a capture inserts
`block_contents` (INSERT OR IGNORE) and its block row in one transaction and must never lose content it just reused; only
requests of other sessions that are *not archived*, and requests without a session, keep a hash alive. Returning freed pages
(`reclaim_space`) must use the raw DBAPI connection (`engine.raw_connection()`): through SQLAlchemy `PRAGMA incremental_vacuum`
closes its result after one step and frees about one page per call. Capture reads stored bodies of the request it continues
(`proxy/addon.py: _DatabaseLineageRepository`, looked up by provider response ID across sessions), so a resumed conversation whose
predecessor was archived is captured with partial context; this is documented behaviour, covered by a test.

Purging (`db/database.py: startup_vacuum`, despite its name) only frees pages inside the file: SQLite never
shrinks the file by itself, so a purged database keeps its size. `contextspy db-compact` (`db/compaction.py`)
runs `VACUUM` offline under the maintenance lock and switches the database to incremental auto-vacuum
(`PRAGMA auto_vacuum = INCREMENTAL`); `init_db` puts new databases in that mode (it must be set before
`journal_mode=WAL` writes the file header and before any table exists), after which `PRAGMA incremental_vacuum`
(its result rows must be fetched for it to do any work) returns freed pages to the filesystem online. Backups are
independent standalone copies and are unaffected by compaction; `pre_compact` is a backup purpose recognised by
`list_backups` and `db-restore`.

### Capture and analysis boundary

Transport ingestion first identifies one externally observable provider invocation. HTTP has one
request/response pair; a registered WebSocket protocol opens an invocation on its provider start
event and closes it on a terminal event. Deltas and utility frames never become request rows.

Provider normalization then produces a standalone `CanonicalInvocation`. For Responses API
traffic, an explicit `previous_response_id` is resolved to a persisted predecessor and the visible
input is expanded as predecessor input + predecessor output + current input. This applies to REST
as well as WebSocket traffic. Only explicit provider IDs authorize this provider-state expansion;
the display graph may infer a parent from captured blocks without expanding provider state.

The session lineage API also computes conservative display conversations. Its raw root-to-leaf
paths are diagnostic paths (the older API field is `lineage_fragment_count`): absent or ambiguous
parent IDs do not split a session into chats.
Separate rows need evidence of a sustained fork with exact diverging predecessor edges, an
overlapping fork with distinct meaningful context, or two disjoint, observed, interleaved chains
with distinct meaningful context. For supported Codex Responses traffic, a source-labelled digest
of a cache hint may also support grouping when substantive context corroborates it; this is not
a provider conversation ID. The persisted `agent` field identifies a product (for example,
`codex`), not a task or subagent; captured requests currently have no reliable dedicated task or
thread identifier. The live dashboard reads totals, lineage, cards, and parent comparisons from
one SQLite snapshot. Its bounded analysis cache is keyed by a fingerprint of relevant request
and block metadata, including matching external provider predecessors; unrelated sessions do
not invalidate it. Diagnostics polls the smaller revision endpoint before reloading a full
graph. See [Conversation tracking architecture](conversation-tracking-architecture.md) for
the phase contracts, cache semantics, and extension points.
See [Request tracking and conversations](request-tracking-and-conversations.md) for the
user-facing interpretation of these groups and their uncertainty markers.

`canonical_request_body` and `canonical_response_body` store the exact JSON documents passed to
the provider adapter. Blocks and category/token columns are derived indexes over those documents;
they intentionally duplicate data. A retained canonical pair is sufficient to rerun analysis
without replaying transport events. `raw_request_body` and `response_events` remain diagnostic
evidence, while `raw_response_body` is retained as a compatibility field.
For Anthropic `thread.continue`, the canonical request is an expanded logical context, while
`raw_request_body` remains the short wire delta. The normalizer follows only
`thread.previous_message_id`; the separate diagnostics predecessor ID compares cache fingerprints.
Inherited system-block tails are treated as uncertain, and server-side context edits may leave
the actual post-edit prompt opaque. A versioned data migration reanalyzes retained thread rows;
requests whose payloads were already removed (archived or purged) cannot be reconstructed from usage numbers.

Reconstruction and block-analysis failures are recorded in `capture_error` without discarding the
canonical application payload. The UI's JSON is provider-level application content, not
byte-identical compressed/chunked network traffic.

The user-facing behavior and storage implications are described in
[REST, streaming, and WebSocket request handling](transport-normalization.md).

### Context fidelity

Each invocation records one of three fidelity states:

- `complete`: the request was observed in full or its visible lineage was expanded through exact
  provider IDs;
- `partial`: a required predecessor was missing or purged, or inherited thread configuration
  cannot be verified exactly;
- `opaque`: compaction, encryption, truncation, or another provider-side representation prevents
  inspection of some content.

Provider-reported usage remains authoritative in every case. Locally tokenized composition
explains the visible canonical JSON; the signed difference can also contain provider-tokenizer or
server-transformation effects, so it is labelled as unattributed/tokenizer difference rather than
invented hidden content. These limits apply to provider-managed REST conversations too, not only
WebSockets.

### Blocks

Every successfully analyzed request/response is also decomposed into `blocks` — one row per
content part (system prompt, tool definition, a single tool call or tool result, a text or
thinking segment, ...).
Each block's semantic `category` (one of the 8 breakdown categories) and structural `block_type`
are kept forever; only the block's `content` (in `block_contents`, deduplicated by content hash
across requests) is removed when a session is archived (or by the opt-in time-based purge above).

### Request purpose, block source and block location

Capture stamps three derived facts, all in Python (`analysis/`), none computed in the UI:

- **`requests.purpose` / `purpose_detail` / `classifier_version`** (`analysis/purpose.py`,
  `classify_request`). The baseline is structural and provider-neutral: it finds the last
  *conversational* message (messages made only of system/developer instructions or reasoning items are
  skipped, because providers append them after the real last turn) and returns `tool_continuation`
  when it carries tool results, `user_turn` when it carries user text, `compaction` when it contains
  a `compaction_trigger` item, otherwise `unknown`. `purpose_detail` adds the trailing tool names,
  whether user text accompanied them, and what the response was (`tool_calls`, `mixed`,
  `final_text`, `empty`). A request whose analysis produced no blocks stays unclassified (NULL).
  To teach it an agent-specific case, call `register_purpose_detector(agents=..., detector=...)`; a
  detector may return any purpose (e.g. `housekeeping`). Bump `CLASSIFIER_VERSION` when the logic
  changes (now 2: file paths) and add a migration that re-derives rows below the new version.
- **`blocks.source_key`** (`analysis/sources.py`, `resolve_sources`): what produced the block. The
  baseline is `system|user|assistant|reasoning|other`, `tool:<name>` and `mcp:<server>/<tool>`.
  Tool calls can be refined by registered parsers (`register_source_parser`), which currently cover
  JSON-argument shell tools (`Bash`) and Codex's JavaScript `exec`/`js` snippets. Parsers return the
  program name only (`bash:git`, `exec:rg`, `exec:multi`) and must never record arguments or other
  command content: the key outlives the block's text. Results inherit the key of their call.
- **`blocks.file_path`** (`analysis/paths.py`, set by `resolve_sources`): the one argument that is kept, the file a
  read/edit tool call targets. Sources: a JSON-object argument (`file_path`, `path`, ... of `Read`, `Edit`,
  `Write`, `read_file`, `str_replace_editor`, ...), the positional files of a closed list of shell programs
  (`cat`, `head`, `tail`, `nl`, `bat`, `less`, `wc`, `stat`, `file`, `sed -n`), and `*** Add/Update/Delete File:`
  headers of an `apply_patch` (patch bodies are never stored). Several files: the first goes in the column, all
  of them in `attrs["source"]["files"]` (max 20). Results inherit the path of their call. Every path passes
  `normalize_file_path`, the only place that decides what is stored: to obfuscate paths later (basename, salted
  hash, a setting) change that function and re-derive; nothing else writes the column. Paths are not resolved
  against a working directory, so relative and absolute spellings of one file differ. They survive archive.
  A parser that raises, or content that was purged, falls back to the baseline.
- **`blocks.json_path`**: typed path into the canonical request (input blocks) or response (output
  blocks) JSON, set by each adapter (`Block.make(..., json_path=(...))`). It points at the smallest
  node the block derives from: a content-part object, the string value for plain-string content, or
  the enclosing container when several parts were joined. `None` means there is no honest location
  (for example the block synthesised for provider-reported reasoning tokens). New adapters must set
  it and add an exact-path test (`tests/test_json_path.py`), which also checks that every path resolves.

`BlockRecord.to_dict` also returns `activity`, derived at read time from `source_key` by
`analysis/activity.py` (a plain table, so it can be refined without a migration).

`contextspy db-upgrade` (schema v9, and v10 for file paths, which re-derives rows below `CLASSIFIER_VERSION` 2 with the same loop) backfills existing rows with the same functions: classification
from the stored block rows, and `json_path` by re-parsing a retained canonical document and copying
paths only when the parse matches the stored blocks exactly (same count, block types and content
hashes per direction). It processes requests in keyset batches, prints progress, and is safe to
re-run.

### Hot spots queries

`db/hotspots_service.py` ranks the blocks, sources or files of a scope by visible tokens carried
(`GET /api/sessions/{id}/hotspots`). One pass groups the scope's input blocks into a TEMP table; rows,
totals, the summary, sorting, paging and the in-context filter come from that table, and only the
returned rows pay a second, bounded lookup (descriptive columns of the latest occurrence by primary
key). Rules to keep when changing it:

- **The scope table must be the outer loop** (`FROM hs_scope s CROSS JOIN blocks b ON b.request_id = s.id`).
  Left free, SQLite walks `idx_blocks_content_hash` for a `GROUP BY content_hash` and is 15-100 times slower.
  `tests/test_hotspots.py` asserts the query plan for all three groupings.
- `aggregate_select` must not depend on sort, paging or the in-context filter, so a cache can wrap it
  later (postponed, GitHub issue #67).
- The latest occurrence of a group is found in the same pass by maximising `position * 2**32 + block id`
  (`analysis/block_hotspots.py: unpack_latest`); run counts come from a `GROUP_CONCAT` of positions.
- Conversation membership comes from `block_occurrence_service.scope_for_session`, shared with the block
  "Present in" panel; it is cached per session (60 s, measured from the end of the build).

---

## Token estimation accuracy

Token counts are **estimates** using tiktoken `o200k_base` encoding
(`analysis/tokenizer.py: ENCODING_NAME`).

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-5.x, GPT-4.1, GPT-4o, o-series) | ~2% — `o200k_base` is these models' native encoder |
| OpenAI (GPT-4, GPT-3.5-turbo) | ~2–5% — these predate `o200k_base` and use `cl100k_base` natively |
| Anthropic (Claude) | ~15–30% — see below |
| Ollama / llama.cpp / vLLM | ~10–20% |

When the provider reports exact token counts in the API response, those are stored
alongside the estimate and shown on the request detail page for comparison.

Inline image, audio, document, and other media payloads need different accounting. A base64 data
URL is only a transport encoding; the provider does not tokenize those characters as prompt text.
ContextSpy therefore replaces media parts with short typed markers in analyzed blocks and marks
mixed blocks with `contains_media` plus `token_estimate: text_only`. The canonical request still
retains the original payload for raw inspection. For multimodal requests, use the provider-reported
input total as the authoritative total because the local estimate covers the visible text but not
provider-specific media tokens.

### Encoder choice, and the 0.3.4 switch

ContextSpy counted with `cl100k_base` up to 0.3.3 and with `o200k_base` from 0.3.4 onwards.
`cl100k_base` is native only to GPT-4 and GPT-3.5-turbo; every OpenAI model released since
GPT-4o — the whole GPT-5.x line, GPT-4.1, the o-series — uses `o200k_base`, so the old
default was an approximation for essentially all current traffic.

The switch was made for correctness, not accuracy: it changes which models the counts are
exact for, not the counts themselves. Re-encoding the captured corpus (~200 KB of real
system prompts, tool definitions, tool results and reasoning) under both encoders gives
totals within **0.0%** of each other, with no category off by more than 3.6%:

| Content kind | `cl100k_base` | `o200k_base` | Difference |
|---|---|---|---|
| Tool results | 17,228 | 17,234 | +0.0% |
| Tool definitions | 10,706 | 10,726 | +0.2% |
| System prompt | 8,228 | 8,221 | −0.1% |
| Thinking | 7,925 | 7,860 | −0.8% |
| Conversation history | 2,541 | 2,560 | +0.7% |
| **All content** | **48,953** | **48,936** | **−0.0%** |

The efficiency gap `o200k_base` is known for shows up on natural language, especially
non-English — not on the English prose, code and JSON that dominate a coding agent's
context. It does nothing for Anthropic, whose tokenizer matches neither encoder.

Every `Request` records which encoder produced its counts in the `tokenizer` column
(`tiktoken/o200k_base`, or `tiktoken/cl100k_base` for rows captured before 0.3.4). Existing
rows are **not** recounted — there is no migration, because the raw bodies needed to redo the
work are removed when a session is archived. Sessions spanning the upgrade therefore mix both,
which given the ~0.0% difference is immaterial in aggregate but is recorded per row should it
ever matter.

### Anthropic tokenizer drift

Anthropic's tokenizer has diverged from `cl100k_base` and now produces materially more
tokens for the same text. Recent Claude requests measured against the provider's own
`usage` show ContextSpy's estimate running **roughly 13–39% low** (small sample of
`claude-haiku-4-5` turns), well outside the ~5–15% this table used to quote. The estimate
is always the low side — tiktoken undercounts, it does not overcount.

This affects the *input* categories too, not just output: every category in the context
breakdown is understated by roughly the same proportion, so the *shares* between categories
stay meaningful even when the absolute numbers are low. Where an exact number matters, use
the provider-reported figures on the request detail page.

It compounds in the `derived` reasoning case below, where the estimate is subtracted from a
provider-reported total — there the whole error is concentrated into the thinking figure
rather than spread across the response.

### Thinking / reasoning tokens

Reasoning is billed by every provider but disclosed by only some, so
`analysis/adapters/base.py: reconcile_thinking()` normalises all of them onto the same
carriers — `thinking` blocks for the text, `provider_reasoning_tokens` for the provider's
own figure — and tags each block with `attrs["token_source"]` recording how it was arrived at:

| `token_source` | When | Accuracy |
|----------------|------|----------|
| `provider` | The API reports a reasoning count (OpenAI `reasoning_tokens`) | Usually exact — but see the Codex caveat below |
| `estimated` | No count, but the text came back (Anthropic `display: "summarized"`, Ollama `thinking`, DeepSeek/vLLM `reasoning_content`) | Same band as the table above |
| `derived` | Neither count nor text (Anthropic `display: "omitted"` — the default on current Claude models — and `redacted_thinking`) | Residual of `output_tokens` minus the estimated visible output |
| `unknown` | Nothing to go on (no count, no text, no `output_tokens`) | Reported as 0 |

The `derived` case matters most in practice: Anthropic's Messages API bills thinking inside
`output_tokens` and never breaks it out under any `thinking.display` setting, so subtraction is
the only signal available. Because the visible side of that subtraction is a tiktoken estimate
against a different tokenizer, **all of its error lands on the thinking figure** — and since
tiktoken tends to undercount Claude's tokenizer, `derived` thinking skews high.

Getting the reasoning *text* captured moves a request off `derived` and onto the more accurate
`estimated` path. For Claude Code that means `"showThinkingSummaries": true` in
`~/.claude/settings.json`; other agents expose it as a `thinking.display: "summarized"` request
parameter. Either way ContextSpy only records what the provider chose to send.

**Codex on a ChatGPT plan — `reasoning_tokens` can describe only the summary.** On the
`chatgpt.com/backend-api/codex/responses` endpoint, the reported `reasoning_tokens` sometimes
covers just the short reasoning summary rather than the hidden reasoning that was actually
billed. Because `provider` outranks every other source, that figure is taken at face value and
the remainder is not attributed anywhere — the request's totals then fall short of
`output_tokens` with no category to account for the difference. Most turns reconcile to within
a few tokens; the failure mode is a turn with heavy hidden reasoning behind a one-line summary
(one observed example: 29 visible + 444 reported reasoning against 2,535 billed output tokens,
leaving 2,062 unaccounted). Compare **Tokens out** with the provider figure on the request
detail page to spot it.

---

## Contributing

1. Fork the repo and create a branch.
2. For backend analysis/capture changes, add or update the relevant test module and confirm
   `pytest` passes.
3. For frontend changes, run `npm test`, rebuild the UI (`make ui`), and verify it in the browser
   with `contextspy start`.
4. Open a pull request against `main` with a description of what changed and why.

Bug reports and feature requests are tracked in [GitHub Issues](https://github.com/RimantasZ/contextspy/issues).
