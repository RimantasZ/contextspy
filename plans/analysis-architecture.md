# Analysis features: architecture review and data-model decisions

Status: architect review, 2026-10-05. Companion to [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md).
Items marked **[PROPOSED]** await user confirmation; items marked **[DECIDED]** follow from roadmap decisions D1–D14
or are forced by measured data. Section 5 lists the schema changes as one batched migration.
User answers of 2026-10-05 (fidelity, tool source, json_path order, retention) are folded in below.

## 1. Measured facts about real data (local DB, read-only, 2026-10-05)

Use these to size designs; re-measure before relying on them.

| Fact | Value | Consequence |
|------|-------|-------------|
| Requests / sessions | 6,865 / 14 | |
| Agents | codex 6,364 (93%), claude_code 502 | **Codex is the primary dataset**, not Claude Code. Designs must not be Anthropic-first. |
| Block rows | 1,351,655 (avg 197/request, max 864) | Never load all blocks of a session into the UI. |
| Largest session | 4,032 requests, 791,039 block rows | Tree, hot spots and "present in" must be server-aggregated, paginated, and lazy. |
| Heaviest single block hash | 32,254 occurrences | "Present in" must **not** return one row per occurrence: return run-length ranges + totals (see §3). |
| Query timings (SQLite, cold) | occurrences of heaviest hash 0.35 s; session-wide hash group-by over 791k rows 0.84 s | Acceptable with existing `idx_blocks_content_hash`; cache by revision like the lineage graph. No `blocks.session_id` denormalisation needed now. |
| Blocks with NULL `content_hash` | 365,401 (27%); 349k are hidden/opaque reasoning inputs | They have token counts but no identity; analysis must treat them as "opaque/unidentifiable", not drop them. |
| Same hash used under >1 block type | 4 hashes of 15,174 | Keeping identity = `content_hash` is safe (no composite key needed). |
| Same hash repeated *within one request* | 31,120 (request, hash) pairs | Occurrence counting must be occurrence-aware (count rows, not distinct requests) — matches `context_diff`'s occurrence-aware matching. |
| Hashes shared across sessions | 1,167 | Always scope identity queries by session. |
| `context_fidelity` | codex: 5,518 opaque, 553 partial, 293 complete; claude_code: 306 complete, 195 partial, 5 opaque | **Most Codex requests are `opaque`** (server-held context via predecessor/WS). Visible blocks are not the whole window. See §4. |
| `predecessor_response_id` | codex 4,666 / 6,364 | Lineage by exact parent is common for Codex. |
| Tool names | Codex: `exec` = 605k of 1.35M block rows, `js`, `send_message`, `wait_agent`, `spawn_agent`… | **Tool-name-based "source" is nearly useless for Codex**: one generic `exec` tool carries everything. Needs args-aware sub-identity (see §2). No `mcp__*` names present in this DB. |
| Block `attrs` | Codex adds `provider_item_type`, `tool_namespace` (e.g. `collaboration`), `hidden`, `opaque` | Source descriptor can use `attrs`. |
| Stored bodies (retained for 1,938 requests) | raw req 181 MB, **canonical req 1,091 MB**, raw resp 175 MB, canonical resp 175 MB, events 442 MB | Bodies dominate DB size (~2 GB); raw==canonical for only 269 of 1,938. Strong motivation for explicit archive (D4). Canonical request bodies exist for only 1,938 of 6,865 requests → `json_path` traceability is available for few historical requests. |
| Cache fields | populated for 464/502 claude_code and 6,223/6,364 codex requests | D2 is cheap to add later (data is there). |

## 2. Data-model decisions

### 2.1 Block identity — [DECIDED] keep `content_hash`
No composite key. Hash-less blocks (27%) are *unidentifiable* and are reported as such (counted in totals, never merged).
Hash-less + `tool_call_id` (tool results with empty output; 9,568 rows) group by `(block_type, tool_call_id)` only inside one request pairing (call↔result linking already exists as `linked_call_id`); no cross-request identity.

### 2.2 Block `source` — [DECIDED (args-aware for exec), **revises** `request-purpose.md`]
`request-purpose.md` proposed deriving `source` on read from `tool_name`. The data refutes that:
- Codex's `exec` hides the real operation in the *arguments*; deriving it needs the tool-call content,
  which is **purged by archive/retention** (D3/D4) — a read-time derivation would silently degrade exactly when analysis must keep working.
- SQL-level grouping (hot spots over 791k rows) needs an indexable column, not a JSON walk in Python per row.

Proposal: **persist** a compact canonical `blocks.source_key` (TEXT, nullable, indexed) computed at capture time and
backfilled best-effort from retained data:
`builtin:Read`, `mcp:<server>/<tool>`, `exec:<program>` (args-aware, e.g. `exec:rg`, `exec:git`), `collab:spawn_agent`, `system`, `user`, `assistant`, `reasoning`, `unknown`.
Richer detail stays in `attrs["source"]` (JSON). The key grammar is documented and versioned by `requests.classifier_version`; unknown/old rows have NULL and render as plain tool name.

**Measured on real `exec` calls (2026-10-05):** Codex `exec` tool_call content is **not JSON arguments**: it is a JavaScript snippet
("code mode"), e.g. `const r = await tools.exec_command({cmd:"sed -n '290,335p' ui/src/api/client.ts", workdir:"...", max_output_tokens:3300}); text(r.output);`,
`const patch="*** Begin Patch ..."` (apply_patch), `await Promise.allSettled([ tools.exec_command(...), ... ])`, `text(await tools.app...)`.
So the parser is a **heuristic over JS source** (find `tools.<name>(` calls, extract the `cmd:` string literal, take the first program of the shell command), not a JSON decode.
A snippet can contain several calls: `source_key` = the primary (first) call, or `exec:multi` when calls differ; the full list goes to `attrs["source"]["calls"]`. Unparseable snippets get `exec:js`.
Only ~31% of distinct input tool_call hashes still have retained content (1,586 of 5,157 for `exec`; 1,894 of 6,041 overall) — **historical backfill will be partial by nature**; capture-time classification is what matters. Add fixtures from real (anonymised) snippets.

**User decision (D12):** args-aware parsing is implemented **for `exec` (and `js`) now**; other tools stay name-based until real data and feedback justify more. The mechanism must make adding a tool-specific parser a one-function change (registry of `tool_name -> parser(args) -> source_key`). The overall goal is to **infer purpose at request level and at individual block level**.

**Block-level purpose** is *derived from `source_key`* through a small mapping table (e.g. `exec:rg|grep|find -> search`, `exec:cat|sed -n|head -> read`, `builtin:Read -> read`, `builtin:Edit|Write|apply_patch -> edit`, `exec:git -> vcs`, `exec:pytest|npm test -> test`, `collab:* -> orchestration`). Only the hard-to-recover input (`source_key`) is persisted; the mapping is code and can be refined without migration. Expose as `block.activity` in API payloads.

### 2.3 Request purpose — [DECIDED] as in `request-purpose.md`
`requests.purpose`, `purpose_detail` (JSON), `classifier_version`. `turn_*` stays derived at read time from the lineage graph (lineage is never persisted). Codex-specific signals to include in detail: orchestration tools (`spawn_agent`, `send_message`, `wait_agent`) → `agent_role_hint`/`orchestration`.

### 2.4 `json_path` on blocks — [DECIDED, priority PROPOSED]
`blocks.json_path` TEXT (JSON array, nullable). Capture-time only; backfill only from retained canonical bodies (≈28% of requests today; archive reduces this further). Because Codex dominates, adapter order should be
**all adapters in one change (user decision D13)**: `openai_responses.py` (495 lines, the Codex path), `anthropic.py`, `openai_chat.py`, `ollama.py`. Verify Codex first since it is the dominant dataset. For WS/delta transports the *canonical* (reconstructed) request body is the path target, not the raw delta.

### 2.5 Archive — [DECIDED, details in draft]
`sessions.archived_at` (nullable). Derived `status` = active|ended|archived. See `unconfirmed_drafts/session-archive.md`.

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
80% of Codex requests are `opaque`: server-held context means the captured blocks are not the full window; provider-reported input
tokens may exceed visible tokens (partial claude_code sessions show visible/provider ratios ≈ 0.34). 
User chose: include opaque/partial requests everywhere, show a per-request fidelity badge, and label every total **"visible tokens"**. The synthetic "unaccounted gap" row was offered and **not** chosen (may be revisited). Rule: occurrence/total token numbers are **visible-block tokens**, labelled as such, never presented as the provider's total.

## 5. Batched schema change (one migration, `SCHEMA_VERSION` 8 → 9) — [PROPOSED]

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
