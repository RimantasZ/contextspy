# Context-window analysis roadmap (entry point)

Status: living document. Created 2026-10-05 from a product brainstorm. **Read this first** if you are
an agent picking up any of the plans listed below; it records the reasoning, the decisions already
taken with the user, and the dependency order between plans.

## Why this roadmap exists

Capture (proxy, adapters, conversations, lineage) is considered good. The next phase is **analysis
of the context window across subsequent requests of one agent conversation**:

- what information is added on each request,
- how long it stays in the context window,
- what its total token cost is,
- and how it could be optimised.

The user wants to act as an expert debugging and analysing LLM context windows. Every plan below
serves one of those four questions.

## Decisions already taken (do not re-litigate without asking the user)

| # | Decision | Notes |
|---|----------|-------|
| D1 | **Cost = tokens only.** No monetary values now or in the near future. | The tool's purpose is comparing token cost. |
| D2 | **No cache-aware cost weighting.** Cache read/write tokens are already stored per request (`requests.cache_read_tokens`, `cache_creation_tokens`) and stay request-level facts. Cache reporting in the new views is **postponed until after the initial implementation**. | Nothing in these plans may allocate cache tokens to blocks. Adding cache columns later is purely additive. |
| D3 | **Analysis must keep working when content is purged.** Block rows (hash, type, tokens) are kept forever; only content/raw bodies go. Purged blocks render as greyed nodes with token counts, never hidden. | |
| D4 | **Retention becomes explicit.** Replace implicit "N days at startup" purge with a session lifecycle `active → ended → archived`. Archive is a user action on the session screen, **one-way, with confirmation**. No auto-archive rule in the first version. | See `session-archive.md` (Plan 3b) and `db-compact.md` (Plan 3a). |
| D5 | **Scope of analysis = per conversation by default, with a switch to whole session**, mirroring the dashboard. Conversation membership **must reuse the existing dashboard/lineage definition** (`db/session_lineage_service.py`, `crud._node_group_key`, `annotated_lineage_nodes`), not a new one. | Lineage breaks (compaction, client restart, uncaptured request) are *events the user wants to see*, not things to hide. |
| D6 | **Drop `plans/postponed/SESSION_ANALYSIS_PLAN.md`** (deleted). It predates lineage, conversations and many other changes. Ideas worth keeping from it are folded into the individual plans (typed `json_path` per block, one bulk query instead of N+1, lazy tokenization of the selected block only, controlled JSON viewer that can reveal a path). | `REQUEST_LINEAGE_PLAN.md` lines ~22 and ~954 still link to the deleted file; fix when that plan is next touched. |
| D7 | **Classification must be extensible**: future work will differentiate tools, MCP servers, subagents, etc. Use coarse stable enums + free-form JSON detail + `classifier_version`, never a growing enum. | See `request-purpose.md`. |
| D8 | **Info panel is the shared surface for actions/insights** on blocks and requests (cost/"present in", compare, etc.). Existing panels (block inspector in Request detail, context-change panel in the conversation view) are enough for now; the tree page gets the panel as its right-hand pane, reacting to node selection. No drawer variant, no new panel framework. | See `info-panel.md`. |
| D9 | **Tree/compare pages must visually match the Request detail page.** Earlier attempts looked bad. Focus: tree representation, node icons, collapse/expand animation, lightweight minimal node labels; details live in the info panel. | See `unconfirmed_drafts/context-tree.md`. |
| D16 | **Provider neutrality (user, 2026-10-05):** requests come from Anthropic, OpenAI, Copilot, Ollama, llama.cpp, vLLM and any OpenAI-compatible API. **No decision may be justified by the contents of the author's local DB** (heavily Codex). Agent/provider-specific logic is a pluggable enhancement over a generic baseline; fixtures must cover every adapter. | `analysis-architecture.md` §0. |
| D11 | **Fidelity:** include `opaque`/`partial` requests in all analysis views, show a per-request fidelity badge, label totals "visible tokens". | See `analysis-architecture.md` §4. |
| D12 | **Source/purpose at request and block level.** Persist `blocks.source_key` computed at capture via a generic baseline plus a pluggable parser registry (parsers shipped: Codex `exec`/`js` and JSON-argument `Bash`; everything else name-based, expand with data/feedback from other agents); derive block `activity` from it via a mapping table. | Revises the "derive on read" idea in `request-purpose.md`: args are purged on archive. |
| D13 | **`json_path` implemented for all adapters in one change.** | |
| D14 | **Retention:** time-based purge defaults to off (`0`), setting kept as legacy; explicit archive is the main path. | Refined by D17: also log a startup notice when a config explicitly enables it. |
| D15 | **Batched schema migration v9** for all new columns (requests purpose fields, blocks `source_key`/`json_path`, sessions `archived_at`). | **Done** (columns, backfill, tests). See `analysis-architecture.md` §5. |
| D17 | **Compaction before archive; incremental auto-vacuum** (user, 2026-10-05). Deleting rows never shrinks a SQLite file (the author's 6.6 GB DB is 65% free pages). `db-compact` (offline VACUUM + converts to `auto_vacuum=INCREMENTAL`) ships first; new databases start incremental; archive then shrinks the file online. | `db-compact.md`. D14 refined: default `0` **plus a startup notice** when a config explicitly enables the purge. |
| D10 | One plan file per feature. Plans that are confirmed live in `plans/`; plans still being refined live in `plans/unconfirmed_drafts/`. | |

## Repository policies that constrain every plan (from `AGENTS.md`)

- **Analysis logic lives in Python** (`analysis/`, `db/`, routers under `api/routers/`). The frontend
  only formats and displays what the API computed; it must not re-derive tokens, aggregates,
  categories, "first seen", "present in", etc.
- Any `db/models.py` change **must** be accompanied by `db/database.py:_migrate()` (additive column)
  and/or `db/migrations.py` (`_migrate_to_vN` + `SCHEMA_VERSION` bump; currently 9) for backfill.
- After changing `ui/src/`, rebuild with `make ui`; run `pytest` and `cd ui && npm test`.
- Lineage/conversation membership is **derived at read time and never persisted**
  (`session_lineage_service.py` header). Do not add persisted columns that encode conversation
  membership (this is why `turn_id` is *derived*, not stored — see `request-purpose.md`).

## Architecture review

[`analysis-architecture.md`](analysis-architecture.md) holds measured data facts (sample-only measurements: a
4,032-request / 791k-block session shows the scale that can occur), the data-model decisions, API contracts (bounded,
run-length occurrences, revision-cached, fidelity-aware) and the batched migration. **Read it before
implementing any plan.** Where it conflicts with an individual plan, it wins and the plan must be updated.

## Existing building blocks (verified 2026-10-05)

- Block identity: `BlockRecord` (`content_hash`, `token_count`, `tool_name`, `tool_call_id`,
  `message_index`, `position`, `attrs` JSON); content deduplicated in `block_contents`.
  Equal non-null `content_hash` within a session = same block (`crud.get_blocks`
  computes `first_seen_session_seq`).
- Lineage and diff: `analysis/lineage.py` (`build_lineage_graph`, `context_diff_for_requests`),
  `analysis/context_diff.py` (occurrence-aware `diff_contexts`: persisted / promoted / replaced / added),
  `db/session_lineage_service.py` (cached derived graph), `crud.annotated_lineage_nodes`
  (`conversation_code`, previous/next request in conversation).
- Block presentation in UI: `ui/src/lib/blockVisuals.ts` (labels, colours, `visualOf`),
  `ui/src/components/request/BlockInspector.tsx` (block info panel in Request detail),
  `ui/src/components/dashboard/ContextChangePanel.tsx` (request-level panel in conversation view),
  `RequestWorkbench`, `ParsedViewer`/`RawViewer`/content viewers under `components/ui/`.
- Request-level cache fields and `context_accounting` already exist (shown in Request detail metadata).
- Prior related plan, implemented: `plans/show-new-block-only.md` (baseline = lineage parent, falling
  back to previous request in the conversation). Reuse its baseline rule wherever a "previous request" is needed.

## What is implemented right now (2026-10-06)

Implemented (WI-0, tests green, not released): schema v9; per-request `purpose`/`purpose_detail`/`classifier_version`; per-block
`source_key` (+ derived `activity`) and `json_path` for all four adapters; the v9 backfill (`contextspy db-upgrade`, ~3 min on a
7k-request database); `GET /api/requests?purpose=`; purpose chip and block Source/Activity/JSON-location rows in Request detail; docs.

**Also implemented (Plan 1; committed in `153a9d0`):** `GET /api/requests/{id}/blocks/{block_id}/occurrences` (+ `/occurrences/requests`), the "Present in" section in the block inspector, and `?block=<id>` selection in Request detail.

**Also implemented (Plan 3b; in the working tree, not committed):** session archive (`POST /api/sessions/{id}/archive`, `contextspy session archive`, Archive button/badge/notice in the UI), session `status`, request `content_state`, retention default `0` with a startup notice, online file shrinking after an archive. Verified on a copy of the author's database; no real database archived.

**Also implemented (Plan 3a; committed in `f34fa29`):** `contextspy db-compact` (offline VACUUM, optional `--backup`), `pre_compact` backups recognised by restore, new databases created in incremental auto-vacuum mode, file-size/free-space lines in `db-stats`. Verified on a copy of the author's database (6.60 → 2.36 GB); **the live database itself has not been compacted.**

**Not implemented:** everything in Plans 4, 5, 6, 7 (hot spots, context tree, compare, hints);
`housekeeping` detection and any agent purpose detectors; source parsers beyond Codex `exec`/`js` and `Bash`; purpose in the request
list/conversation cards; any use of `json_path` beyond displaying it; cache reporting (D2); (the retention-default change and startup notice, D14, are part of Plan 3b and are implemented).
Nothing from this roadmap has been released, and the real database has not been upgraded.

**Known issue to resolve:** the author's live DB already contains a `blocks.json_path` column with 6,794 values in a different (leaf-level)
convention from an earlier prototype; see `wi0-data-foundation.md` §17 "Findings".

## Plans and order

> **Naming:** *Plans 1–7* are the feature plans in the table below. *WI-0* is the data-foundation work item that precedes them; its own sub-steps are called *slices 1–5* inside `wi0-data-foundation.md`. "Slice" never refers to a numbered plan.

```
                ┌────────────────────┐
                │ 1 info-panel       │──────────────┐
                └────────────────────┘              │
                ┌────────────────────┐              ▼
                │ 2 request-purpose  │────►  5 context-tree ───► 6 request-compare
                └────────────────────┘              ▲
                ┌────────────────────┐              │
                │ 3 compact+archive  │──► 4 hot-spots ──► 7 optimisation-hints (later)
                └────────────────────┘
```

| # | Plan | File | Status | Depends on |
|---|------|------|--------|-----------|
| 0 | Data foundation: migration v9, capture-time classification, `json_path` | [`wi0-data-foundation.md`](wi0-data-foundation.md) | **implemented and committed** (`528f3db`, `88bd6e0`); not released; no browser check or real-DB `db-upgrade` yet | — |
| 1 | Info panel: "present in" / totals for a block | [`info-panel.md`](info-panel.md) | **implemented and committed** (`153a9d0`); not released, not checked in a browser; shared-pieces extraction was dropped in review | WI-0 |
| 2 | Request purpose & extensible classification | [`request-purpose.md`](request-purpose.md) → implemented by WI-0 | **baseline implemented in WI-0** (`user_turn`, `tool_continuation`, `compaction`, `unknown`; `housekeeping` and agent detectors NOT implemented; UI shows it in Request detail only) | — |
| 3a | `contextspy db-compact` (reclaim free pages, enable incremental auto-vacuum) | [`db-compact.md`](db-compact.md) | **implemented and committed** (`f34fa29`); not released; the author's live DB not yet compacted | — |
| 3b | Session lifecycle & explicit archive | [`session-archive.md`](session-archive.md) | **implemented** (uncommitted, not released, not checked in a browser); only the `sessions.archived_at` column exists (WI-0), nothing sets it | 3a |
| 4 | Hot spots (per conversation / per session) | [`unconfirmed_drafts/hot-spots.md`](unconfirmed_drafts/hot-spots.md) | draft | 1, 3b |
| 5 | Context tree page | [`unconfirmed_drafts/context-tree.md`](unconfirmed_drafts/context-tree.md) | draft | 1, 2 |
| 6 | Request compare | [`unconfirmed_drafts/request-compare.md`](unconfirmed_drafts/request-compare.md) | draft | 5 |
| 7 | Optimisation hints ("carried but dead") | [`unconfirmed_drafts/optimisation-hints.md`](unconfirmed_drafts/optimisation-hints.md) | idea only | 4 |

Plans 1, 2 and 3 are independent and can proceed in parallel. 1 and 2 set contracts others use,
so they were written first.

## How to continue

**WI-0 ([`wi0-data-foundation.md`](wi0-data-foundation.md)) is implemented**; its §17 is the authoritative record of what exists and how it
differs from its own spec. Plan 1 (info panel "present in"), Plan 3a (`db-compact`) and Plan 3b (archive) are implemented too. Plans 4 (hot spots) and 5 (context tree) are drafts: their open questions need answers before they can be specified. Plan 4 depends on 1 and 3b (both implemented), Plan 5 on 1 and 2.
WI-0 follow-ups that still wait for captures from Copilot, Ollama, llama.cpp and vLLM: housekeeping/compaction detectors per agent and more
source parsers. Review the `compaction_trigger` rule (a judgement call) and decide what to do with the legacy leaf-form `json_path` values in
the author's DB (see WI-0 §17) before treating either as settled.

1. Read this file, then the plan you are asked to work on, then `AGENTS.md`.
2. If the plan is a **draft**, it is *not* an approved spec: list its "Open questions", ask the user,
   fold the answers in, then move the file from `unconfirmed_drafts/` to `plans/` and update the
   status table above.
3. Verify every code reference in a plan before relying on it; plans record the state on 2026-10-05.
4. When a plan is implemented, mark its status here and in the plan, update `SPEC.md`,
   `docs/development.md`, `docs/changelog.md` as relevant.

## Cross-cutting open questions

- Verify all adapters (Anthropic, OpenAI chat/responses, Ollama) populate cache fields consistently
  (follow-up, after initial implementation; see D2).
- Definition of "dead weight" for hints (plan 7) needs product input.
