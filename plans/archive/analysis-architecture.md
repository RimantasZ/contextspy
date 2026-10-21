# Analysis features: architecture review and data-model decisions

Status: architect review, 2026-10-05; **its decisions are implemented** (WI-0, Plans 1, 3, 4; see "What changed after implementation" at the end). Companion to [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). The measurements below are 2026-10-05 samples.
Items marked **[PROPOSED]** await user confirmation; items marked **[DECIDED]** follow from roadmap decisions D1–D14
or are forced by measured data. Section 5 lists the schema changes as one batched migration.
User answers of 2026-10-05 (fidelity, tool source, json_path order, retention) are folded in below.

## 0. Ground rule: no design decision may depend on one user's database

ContextSpy captures Anthropic, OpenAI (chat + responses), GitHub Copilot, Ollama, llama.cpp, vLLM and any
OpenAI-compatible API, from many different agents. The author's local DB (below) is **one sample, heavily Codex**.
It is used only to (a) sanity-check performance order of magnitude and (b) show that certain *shapes* of data exist.
Every rule in this document must be justified by the wire formats/adapters and by the shape being *possible*,
not by how common it is locally. Agent- or provider-specific logic (e.g. Codex `exec` parsing) is an **optional,
pluggable enhancement** on top of a generic baseline that works for every provider.

## 1. Sample measurements (author's local DB, read-only, 2026-10-05) — illustrative only

Do not size, prioritise or specialise on these; they show shapes that can occur.

| Fact | Value | Consequence |
|------|-------|-------------|
| Requests / sessions | 6,865 / 14 | |
| Agents in this sample | codex 6,364, claude_code 502 | Sample bias. Designs must be provider-neutral; test fixtures must cover every adapter. |
| Block rows | 1,351,655 (avg 197/request, max 864) | Never load all blocks of a session into the UI. |
| Largest session | 4,032 requests, 791,039 block rows | Tree, hot spots and "present in" must be server-aggregated, paginated, and lazy. |
| Heaviest single block hash | 32,254 occurrences | "Present in" must **not** return one row per occurrence: return run-length ranges + totals (see §3). |
| Query timings (SQLite, cold) | occurrences of heaviest hash 0.35 s; session-wide hash group-by over 791k rows 0.84 s | Acceptable with existing `idx_blocks_content_hash`; cache by revision like the lineage graph. No `blocks.session_id` denormalisation needed now. |
| Blocks with NULL `content_hash` | 365,401 (27%); 349k are hidden/opaque reasoning inputs | They have token counts but no identity; analysis must treat them as "opaque/unidentifiable", not drop them. |
| Same hash used under >1 block type | 4 hashes of 15,174 | Keeping identity = `content_hash` is safe (no composite key needed). |
| Same hash repeated *within one request* | 31,120 (request, hash) pairs | Occurrence counting must be occurrence-aware (count rows, not distinct requests) — matches `context_diff`'s occurrence-aware matching. |
| Hashes shared across sessions | 1,167 | Always scope identity queries by session. |
| `context_fidelity` | codex: 5,518 opaque, 553 partial, 293 complete; claude_code: 306 complete, 195 partial, 5 opaque | Opaque/partial context is **possible for any provider** that supports server-held state (OpenAI Responses `previous_response_id`, WS delta transports) or lossy capture. Visible blocks are then not the whole window. See §4. |
| `predecessor_response_id` | codex 4,666 / 6,364 | Lineage by exact parent is common for Codex. |
| Tool names | one agent funnels most work through a generic `exec` wrapper tool (605k of 1.35M rows) | Shows that **a generic tool wrapper can hide the real operation in its arguments**; tool-name-only attribution can be too coarse for such agents. Other agents (Claude Code: `Read`/`Edit`/`Bash`, MCP `mcp__server__tool`) are well described by name. No MCP names in this sample, but the format must be supported. |
| Block `attrs` | adapters add provider-specific keys (`provider_item_type`, `tool_namespace`, `hidden`, `opaque`, `cache_control`, `is_prefill`) | Source descriptor may use `attrs`; keys differ per adapter. |
| Stored bodies (retained for 1,938 requests) | raw req 181 MB, **canonical req 1,091 MB**, raw resp 175 MB, canonical resp 175 MB, events 442 MB | Bodies dominate DB size (~2 GB); raw==canonical for only 269 of 1,938. Strong motivation for explicit archive (D4). Canonical request bodies exist for only 1,938 of 6,865 requests (7-day retention default) → `json_path` can only be backfilled where bodies survive. |
| Cache fields | populated for most Anthropic and OpenAI requests in the sample | D2 is cheap to add later; availability varies by provider (Ollama/llama.cpp/vLLM may report none). |

## 2. Data-model decisions

### 2.1 Block identity — [DECIDED] keep `content_hash`
No composite key. Hash-less blocks (27%) are *unidentifiable* and are reported as such (counted in totals, never merged).
Hash-less + `tool_call_id` (tool results with empty output; 9,568 rows) group by `(block_type, tool_call_id)` only inside one request pairing (call↔result linking already exists as `linked_call_id`); no cross-request identity.

### 2.2 Block `source` — [DECIDED, provider-neutral baseline + pluggable parsers; **revises** `request-purpose.md`]
`request-purpose.md` proposed deriving `source` on read from `tool_name`. That is insufficient in general:
- Some agents use generic wrapper tools (e.g. `exec`, `bash`, `shell`, `run`, or MCP gateways) whose real operation lives in the *arguments*; deriving it needs the tool-call content,
  which is **purged by archive/retention** (D3/D4) — a read-time derivation would silently degrade exactly when analysis must keep working.
- SQL-level grouping (hot spots over 791k rows) needs an indexable column, not a JSON walk in Python per row.

Proposal: **persist** a compact canonical `blocks.source_key` (TEXT, nullable, indexed) computed at capture time and
backfilled best-effort from retained data:
`tool:<name>` (generic baseline for any provider), `mcp:<server>/<tool>`, `<wrapper>:<program>` (from a pluggable args-aware parser, e.g. `exec:rg`, `bash:git`), `system`, `user`, `assistant`, `reasoning`, `unknown`. Never assume a specific agent's tool names in the baseline.
Richer detail stays in `attrs["source"]` (JSON). The key grammar is documented and versioned by `requests.classifier_version`; unknown/old rows have NULL and render as plain tool name.

**Parser registry, not special cases.** Source derivation is `resolve_source(block, request) -> source_key` with (1) a generic baseline (`tool:<name>`, MCP name splitting for the known conventions `mcp__server__tool` and namespaced forms from `attrs`), and (2) a registry of optional parsers keyed by (agent/adapter, tool name). Adding support for a new agent or tool = registering one function plus fixtures; nothing else changes.

**Example parser (Codex `exec`, the only args-aware parser planned now):** in the author's sample, `exec` tool_call content is **not JSON arguments**: it is a JavaScript snippet
("code mode"), e.g. `const r = await tools.exec_command({cmd:"sed -n '290,335p' ui/src/api/client.ts", workdir:"...", max_output_tokens:3300}); text(r.output);`,
`const patch="*** Begin Patch ..."` (apply_patch), `await Promise.allSettled([ tools.exec_command(...), ... ])`, `text(await tools.app...)`.
So the parser is a **heuristic over JS source** (find `tools.<name>(` calls, extract the `cmd:` string literal, take the first program of the shell command), not a JSON decode.
A snippet can contain several calls: `source_key` = the primary (first) call, or `exec:multi` when calls differ; the full list goes to `attrs["source"]["calls"]`. Unparseable snippets get `exec:js`.
In the sample only ~31% of distinct input tool_call hashes still have retained content (1,586 of 5,157 for `exec`; 1,894 of 6,041 overall) — **historical backfill will be partial by nature**; capture-time classification is what matters. Add fixtures from real (anonymised) snippets of **each** supported agent.

**User decision (D12):** args-aware parsing is implemented **for Codex `exec`/`js` now**, as the first registry entry; other tools/agents stay name-based until real data from *other* providers and feedback justify more. Bash-style wrappers (Claude Code `Bash`, Copilot terminal tools) are the obvious next candidates, to be specified from fixtures of those agents, not guessed. The overall goal is to **infer purpose at request level and at individual block level**.

**Block-level purpose** is *derived from `source_key`* through a small mapping table (e.g. `exec:rg|grep|find -> search`, `exec:cat|sed -n|head -> read`, `builtin:Read -> read`, `builtin:Edit|Write|apply_patch -> edit`, `exec:git -> vcs`, `exec:pytest|npm test -> test`, `collab:* -> orchestration`). Only the hard-to-recover input (`source_key`) is persisted; the mapping is code and can be refined without migration. Expose as `block.activity` in API payloads.

### 2.3 Request purpose — [DECIDED] as in `request-purpose.md`
`requests.purpose`, `purpose_detail` (JSON), `classifier_version`. `turn_*` stays derived at read time from the lineage graph (lineage is never persisted). Agent-specific signals (e.g. orchestration tools such as `spawn_agent`, Claude Code `Task`, title-generation side calls) go through the same pluggable-registry mechanism; the baseline structural rule (trailing user text vs trailing tool results; response kind) must work for every adapter.

### 2.4 `json_path` on blocks — [DECIDED, priority PROPOSED]
`blocks.json_path` TEXT (JSON array, nullable). Capture-time only; backfill only from retained canonical bodies (≈28% of requests today; archive reduces this further). Implement for **all adapters in one change (user decision D13)**: `anthropic.py`, `openai_chat.py`, `openai_responses.py`, `ollama.py` (llama.cpp/vLLM/Copilot ride the OpenAI-compatible paths — confirm which adapter each uses via `get_adapter` path dispatch). Each adapter needs exact-path fixtures. For WS/delta transports the *canonical* (reconstructed) request body is the path target, not the raw delta.

### 2.5 Archive — [DECIDED, details in draft]
`sessions.archived_at` (nullable). Derived `status` = active|ended|archived. See `session-archive.md` and `db-compact.md`.

### 2.6 Things deliberately **not** changed
- No `blocks.session_id` (join via `requests.session_id` is fast enough: 0.15–0.84 s on the largest session).
- No persisted lineage/conversation/turn data (existing architectural rule).
- No per-block cache allocation (D2).
- No persisted per-request "new tokens" aggregates yet: they derive from the cached lineage graph + `context_diff`. Revisit only if tree/hot-spot latency on the 4k-request session is unacceptable after measurement.
- `tool_stats` table is redundant with blocks but used by existing views; leave.

## 3. API/aggregation contracts (apply to plans 1, 4, 5, 6)

1. **Bounded responses**: every list endpoint has a limit/cursor; no endpoint returns O(requests × blocks).
2. **Run-length occurrences**: occurrence lists are returned as ranges of `session_seq`
   (`[{"from": 12, "to": 40, "count": 29}, {"from": 44, "to": 44, "count": 1}]`) plus totals; the UI expands a range lazily
   (`/occurrences?range=…`) for navigation. Per-request detail is returned only for ≤ N occurrences (N≈50).
3. **Occurrence-aware** counts (rows, not distinct requests); within-request duplicates reported.
4. **Revision-cached** heavy aggregates (hot spots, tree structure) keyed like `session_lineage_service.evidence_revision` (include `classifier_version`).
5. **Fidelity-aware** (§4): every analysis response carries per-request `context_fidelity` summary so the UI can badge it; it never silently treats visible blocks as the whole window.
6. **Content-optional** (D3): all analysis endpoints work with `content_purged` blocks.
7. Analysis logic in Python (`analysis/…`), SQL only fetches rows/aggregates.

## 4. Fidelity (opaque / partial) — [DECIDED: include, badge, label as visible tokens (D11)]
In the author's sample most Codex requests are `opaque`, and some partial Claude Code requests show visible/provider ratios ≈ 0.34. The general point: whenever context is held server-side or capture is lossy, visible tokens can be well below provider-reported tokens. 
User chose (provider-neutral rule): include opaque/partial requests everywhere, show a per-request fidelity badge, and label every total **"visible tokens"**. The synthetic "unaccounted gap" row was offered and **not** chosen (may be revisited). Rule: occurrence/total token numbers are **visible-block tokens**, labelled as such, never presented as the provider's total.

## 5. Batched schema change (one migration, `SCHEMA_VERSION` 8 → 9) — [DECIDED; **implemented 2026-10-05**: columns, indexes and backfill — see `wi0-data-foundation.md` §17]

Do all at once to avoid repeated migrations while this area is under development:

| Table | Change | Backfill |
|-------|--------|----------|
| `requests` | `purpose TEXT`, `purpose_detail TEXT`, `classifier_version INTEGER` | derive from retained block rows (no raw bodies needed) |
| `blocks` | `source_key TEXT` (+ index), `json_path TEXT` | `source_key`: from tool_name/attrs/retained tool-call content (best effort, NULL if unrecoverable); `json_path`: only from retained canonical body, unambiguous matches only |
| `sessions` | `archived_at DATETIME` | none |
| indexes | `idx_blocks_source_key`; consider `(request_id, direction, position)` only if a measured need appears | — |

Mechanics (per `AGENTS.md`): additive columns in `db/database.py:_migrate()`; backfill `_migrate_to_v9` in `db/migrations.py` with keyset batches (v8 is the pattern), idempotent via `classifier_version`; `tests/test_migrations.py` case; backup behaviour via existing `db-upgrade`. Backfill cost: ~1.35M block rows — must be batched, resumable, and report progress.

## 6. Delivery order revision
1. Migration v9 + capture-time classification (`purpose`, `source_key`, `json_path` for Responses adapter) — the data-model foundation.
2. Info panel "present in" (plan 1) using run-length occurrences.
3. Session archive (plan 3) — unblocks storage and honest "purged" states.
4. Hot spots (plan 4).
5. Tree (plan 5), then compare (plan 6), then hints (plan 7).

## 7. What changed after implementation (2026-10-06)
- **Schema is v10, not v9.** v9 is the batched migration of section 5. v10 added `blocks.file_path` (+ index) and re-derives source keys/paths for requests below `CLASSIFIER_VERSION` 2 (decision D18 reversed the "program names only" rule for that one field; see `file-paths.md`). The two share one batched backfill (`migrations._backfill_classification`).
- **Delivery order (section 6) was followed**: v9 + classification, info panel, archive (with `db-compact` first), file paths, hot spots. Hot spots is a view of a *session*, not a request-level page.
- **Contract 4 (revision-cached heavy aggregates) was not applied to hot spots**: one aggregation pass fits the budget (~1.1 s for 4,032 requests). Only the conversation *membership* is cached (60 s, per session, keyed by request count). A hot-spots cache is postponed (issue #67); the aggregation is shaped so one can wrap it.
- **Contract 2 (run-length occurrences) held** for "Present in"; hot spots add per-row `run_count` and a `latest` pointer instead of listing occurrences.
- **Provider neutrality (D16) held**: parsers shipped are Codex `exec`/`js`, JSON-argument `Bash`, and generic structured read/edit tools (`Read`, `read_file`, `str_replace_editor`, ...). No Copilot, llama.cpp or vLLM captures were available; Ollama's adapter emits no tool-call blocks at all.
- **New performance facts**: the cold conversation membership (existing lineage analysis) costs 65 s on a 4,032-request session; listing requests of an unarchived session is slow because of large inline body columns. Neither was caused by this work; both limit how fast conversation-scoped features can feel. Measure before building more on them.
