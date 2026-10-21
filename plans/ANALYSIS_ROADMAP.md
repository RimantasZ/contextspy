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
| D4 | **Retention becomes explicit.** Replace implicit "N days at startup" purge with a session lifecycle `active → ended → archived`. Archive is a user action on the session screen, **one-way, with confirmation**. No auto-archive rule in the first version. | See `archive/session-archive.md` (Plan 3b) and `archive/db-compact.md` (Plan 3a). |
| D5 | **Scope of analysis = per conversation by default, with a switch to whole session**, mirroring the dashboard. Conversation membership **must reuse the existing dashboard/lineage definition** (`db/session_lineage_service.py`, `crud._node_group_key`, `annotated_lineage_nodes`), not a new one. | Lineage breaks (compaction, client restart, uncaptured request) are *events the user wants to see*, not things to hide. |
| D6 | **Drop `SESSION_ANALYSIS_PLAN.md`** (deleted from `plans/postponed/`). It predates lineage, conversations and many other changes. Ideas worth keeping from it are folded into the individual plans (typed `json_path` per block, one bulk query instead of N+1, lazy tokenization of the selected block only, controlled JSON viewer that can reveal a path). | `REQUEST_LINEAGE_PLAN.md`'s links to the deleted file were replaced by plain text on 2026-10-06. |
| D7 | **Classification must be extensible**: future work will differentiate tools, MCP servers, subagents, etc. Use coarse stable enums + free-form JSON detail + `classifier_version`, never a growing enum. | See `archive/request-purpose.md`. |
| D8 | **Info panel is the shared surface for actions/insights** on blocks and requests (cost/"present in", compare, etc.). Existing panels (block inspector in Request detail, context-change panel in the conversation view) are enough for now; the tree page gets the panel as its right-hand pane, reacting to node selection. No drawer variant, no new panel framework. | See `archive/info-panel.md`. |
| D9 | **Tree/compare pages must visually match the Request detail page.** Earlier attempts looked bad. Focus: tree representation, node icons, collapse/expand animation, lightweight minimal node labels; details live in the info panel. | See `unconfirmed_drafts/context-tree.md`. |
| D16 | **Provider neutrality (user, 2026-10-05):** requests come from Anthropic, OpenAI, Copilot, Ollama, llama.cpp, vLLM and any OpenAI-compatible API. **No decision may be justified by the contents of the author's local DB** (heavily Codex). Agent/provider-specific logic is a pluggable enhancement over a generic baseline; fixtures must cover every adapter. | `archive/analysis-architecture.md` §0. |
| D11 | **Fidelity:** include `opaque`/`partial` requests in all analysis views, show a per-request fidelity badge, label totals "visible tokens". | See `archive/analysis-architecture.md` §4. |
| D12 | **Source/purpose at request and block level.** Persist `blocks.source_key` computed at capture via a generic baseline plus a pluggable parser registry (parsers shipped: Codex `exec`/`js` and JSON-argument `Bash`; everything else name-based, expand with data/feedback from other agents); derive block `activity` from it via a mapping table. | Revises the "derive on read" idea in `archive/request-purpose.md`: args are purged on archive. |
| D13 | **`json_path` implemented for all adapters in one change.** | |
| D14 | **Retention:** time-based purge defaults to off (`0`), setting kept as legacy; explicit archive is the main path. | Refined by D17: also log a startup notice when a config explicitly enables it. |
| D15 | **Batched schema migration v9** for all new columns (requests purpose fields, blocks `source_key`/`json_path`, sessions `archived_at`). | **Done** (columns, backfill, tests). See `archive/analysis-architecture.md` §5. |
| D17 | **Compaction before archive; incremental auto-vacuum** (user, 2026-10-05). Deleting rows never shrinks a SQLite file (the author's 6.6 GB DB is 65% free pages). `db-compact` (offline VACUUM + converts to `auto_vacuum=INCREMENTAL`) ships first; new databases start incremental; archive then shrinks the file online. | `archive/db-compact.md`. D14 refined: default `0` **plus a startup notice** when a config explicitly enables the purge. |
| D18 | **File paths are stored** (user, 2026-10-06; "the retention policy was a mistake anyway"). Read/edit tool calls get their target file in `blocks.file_path`, persisting after archive; every other argument stays unstored. This reverses WI-0's "program names only" rule for that one field. A single function (`analysis/paths.py: normalize_file_path`) is the only writer, so obfuscation (basename/hash/setting) can be added later if paths become a problem. | `archive/file-paths.md`. |
| D19 | **Hot-spots cache is postponed** (user, 2026-10-06). 4b ships without it; the grouping is one function (`aggregate_select`) so a cache can wrap it later. | `postponed/hot-spots-cache.md`, GitHub issue #67 (`perf:` prefix for performance issues). |
| D20 | **Similarity grouping (changed-and-reloaded blocks, version timelines) is v2** (user, 2026-10-06). Hot spots group by **exact content hash** only. | Plan 8 draft. |
| D21 | **Hot-spots semantics** (architect, agreed): identity = `content_hash`; input blocks only; tokens are *visible* tokens; file rows split `result_tokens` (reads) from `call_tokens` (edits/patch text) because patches are carried too; relative and absolute spellings of one file are **not** unified (the proxy does not see the working directory). | `archive/hot-spots.md`. |
| D22 | **Category filter is in the hot-spots UI** (user, 2026-10-06), using the labels the dashboard already has (`ContextBar.tsx`). My first reason for leaving it out (UI must not know the vocabulary) was wrong. | |
| D23 | **Sequence after this work** (user, 2026-10-06): merge `analysis_revamp`, produce a release for users to test, then continue the draft plans (5-8) and UI styling fixes (the Hot spots page has styling remarks pending) once the plans are sorted out. | See "Release readiness". |
| D24 | **`compaction_trigger` purpose rule stays** (user, 2026-10-06): a request whose last conversational message contains a provider `compaction_trigger` item (OpenAI Responses) is `compaction`. It is a wire-format signal, not an agent heuristic. Other agents' compaction (e.g. Claude Code's summarisation prompt looks like `user_turn`) is not detected until captures justify an agent detector. | `archive/wi0-data-foundation.md` §17. |
| D25 | **Legacy leaf-form `blocks.json_path` rows stay as they are** (user, 2026-10-06): 6,794 blocks from an earlier prototype in the author's own database; valid paths, only more specific, never overwritten. No release impact. Revisit only if the context tree needs one convention. | `archive/wi0-data-foundation.md` §17. |
| D10 | One plan file per feature. Plans that are confirmed live in `plans/`; plans still being refined live in `plans/unconfirmed_drafts/`. | |

## Repository policies that constrain every plan (from `AGENTS.md`)

- **Analysis logic lives in Python** (`analysis/`, `db/`, routers under `api/routers/`). The frontend
  only formats and displays what the API computed; it must not re-derive tokens, aggregates,
  categories, "first seen", "present in", etc.
- Any `db/models.py` change **must** be accompanied by `db/database.py:_migrate()` (additive column)
  and/or `db/migrations.py` (`_migrate_to_vN` + `SCHEMA_VERSION` bump; currently 10) for backfill.
- After changing `ui/src/`, rebuild with `make ui`; run `pytest` and `cd ui && npm test`.
- Lineage/conversation membership is **derived at read time and never persisted**
  (`session_lineage_service.py` header). Do not add persisted columns that encode conversation
  membership (this is why `turn_id` is *derived*, not stored — see `archive/request-purpose.md`).

## Architecture review

[`archive/analysis-architecture.md`](archive/analysis-architecture.md) holds measured data facts (sample-only measurements: a
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
- Prior related plan, implemented: `plans/archive/show-new-block-only.md` (baseline = lineage parent, falling
  back to previous request in the conversation). Reuse its baseline rule wherever a "previous request" is needed.

## What is implemented right now (2026-10-06, everything below is committed on branch `analysis_revamp`)

Schema is **v10**. Tests at this point: 685 backend, 199 frontend, `npm run check` clean. **Nothing is released, none of the new UI has been reviewed in a browser by the author, and the author's live database has not been upgraded, compacted or archived by any agent.**

| Area | What exists | Commits |
|------|-------------|---------|
| WI-0 (data foundation, schema v9) | `requests.purpose/purpose_detail/classifier_version`; `blocks.source_key` (+ derived `activity`) and `blocks.json_path` for all four adapters; `sessions.archived_at`; the v9 backfill (`contextspy db-upgrade`); `GET /api/requests?purpose=`; purpose chip and Source/Activity/JSON-location rows in Request detail | `528f3db`, `88bd6e0` |
| Plan 1 (info panel) | `GET /api/requests/{id}/blocks/{block_id}/occurrences` (+ `/occurrences/requests`), "Present in" section in the block inspector, `?block=<id>` deep link in Request detail | `153a9d0` |
| Plan 3a (`db-compact`) | offline VACUUM with optional `--backup`, `pre_compact` backups, new databases in incremental auto-vacuum, size/free-space lines in `db-stats`. Verified on a copy (6.60 to 2.36 GB) | `f34fa29` |
| Plan 3b (archive) | `POST /api/sessions/{id}/archive`, `contextspy session archive`, Archive button/badge/notice, `Session.status`, `Request.content_state`, retention default `0` with startup notice, online file shrinking | `a2a4b8c` |
| Plan 4a (file paths, schema v10) | `blocks.file_path` for read/edit tool calls and their results (`analysis/paths.py` is the single writer), `_migrate_to_v10`, "File" row in the block inspector; fixed `list_backups` ordering (two-digit schema versions) | `d608561` |
| Plan 4b (hot spots) | `GET /api/sessions/{id}/hotspots` and the **Hot spots** session view (blocks / sources / files; conversation or whole session; sort; category, block-type and in-context filters; click-through to the request with the block selected; Conversations view links); `block_occurrence_service.scope_for_session`; fixed the membership cache lifetime | `e6c4396`, `4cb4c84` (category filter) |

Facts worth knowing about the data, measured on a copy of the author's database (samples, not rules, D16): upgrading 7.2k requests / 1.5M blocks from schema 8 takes ~2-3 min (v9 117-174 s, v10 alone 104 s); 8.4% of blocks have a `file_path`; hot-spots aggregation ~1.1 s on a 4,032-request session; **the existing conversation (lineage) analysis costs 65 s cold on that session** and every conversation-scoped feature inherits it.

**Not implemented:** Plans 5, 6, 7, 8 (context tree, compare, hints, similarity); `housekeeping` purpose detection and any agent purpose detectors; turn grouping (`turn_index`; specified in `archive/request-purpose.md`, needed by the tree's "by turn" mode); source/file parsers beyond Codex `exec`/`js`, `Bash` and the generic structured tools (no MCP file tools); Ollama tool-call blocks (its adapter emits none, so no sources/paths there); purpose in the request list and conversation cards; jumping from a block's JSON location to the raw JSON viewer; cache reporting (D2); the hot-spots cache (D19).

## Plans and order

> **Naming:** *Plans 1–7* are the feature plans in the table below. *WI-0* is the data-foundation work item that precedes them; its own sub-steps are called *slices 1–5* inside `archive/wi0-data-foundation.md`. "Slice" never refers to a numbered plan.

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
| 0 | Data foundation: migration v9, capture-time classification, `json_path` | [`archive/wi0-data-foundation.md`](archive/wi0-data-foundation.md) | **implemented and committed** (`528f3db`, `88bd6e0`); not released; no browser check or real-DB `db-upgrade` yet | — |
| 1 | Info panel: "present in" / totals for a block | [`archive/info-panel.md`](archive/info-panel.md) | **implemented and committed** (`153a9d0`); not released, not checked in a browser; shared-pieces extraction was dropped in review | WI-0 |
| 2 | Request purpose & extensible classification | [`archive/request-purpose.md`](archive/request-purpose.md) → implemented by WI-0 | **baseline implemented in WI-0** (`user_turn`, `tool_continuation`, `compaction`, `unknown`; `housekeeping` and agent detectors NOT implemented; UI shows it in Request detail only) | — |
| 3a | `contextspy db-compact` (reclaim free pages, enable incremental auto-vacuum) | [`archive/db-compact.md`](archive/db-compact.md) | **implemented and committed** (`f34fa29`); not released; the author's live DB not yet compacted | — |
| 3b | Session lifecycle & explicit archive | [`archive/session-archive.md`](archive/session-archive.md) | **implemented and committed** (`a2a4b8c`); not released, not checked in a browser; only the `sessions.archived_at` column exists (WI-0), nothing sets it | 3a |
| 4a | Capture the file a block is about (`blocks.file_path`, schema v10) | [`archive/file-paths.md`](archive/file-paths.md) | **implemented and committed** (`d608561`); not released; not seen in a browser | WI-0 |
| 4b | Hot spots (per conversation / per session; by block, source, file) | [`archive/hot-spots.md`](archive/hot-spots.md) | **implemented and committed** (`e6c4396`, category filter `4cb4c84`); not released; profiler-style layout (bars, expandable rows, `layout=name|bar` comparison toggle) implemented, uncommitted, see [`archive/hot-spots-layout.md`](archive/hot-spots-layout.md); cache postponed ([`postponed/hot-spots-cache.md`](postponed/hot-spots-cache.md), issue #67) | 1, 3b, 4a |
| 5 | Context tree page | [`unconfirmed_drafts/context-tree.md`](unconfirmed_drafts/context-tree.md) | **draft** (updated 2026-10-06 with what the code now offers; open questions unanswered) | 1, 2 (needs turn grouping), 4a/4b helpers |
| 6 | Request compare | [`unconfirmed_drafts/request-compare.md`](unconfirmed_drafts/request-compare.md) | **draft** (updated 2026-10-06) | 5 |
| 7 | Optimisation hints ("carried but dead") | [`unconfirmed_drafts/optimisation-hints.md`](unconfirmed_drafts/optimisation-hints.md) | **idea only** (inputs from 4b now exist; see the draft) | 4b |
| 8 | Similarity grouping: changed-and-reloaded blocks, version timelines (**v2**) | [`unconfirmed_drafts/similarity-grouping.md`](unconfirmed_drafts/similarity-grouping.md) | idea only, postponed to v2 | 4a, 4b, 6 |
| 9 | **(ask the user for a decision when starting this)** More hot-spots groupings: block types, commands, invocations (call + result as one unit) | [`unconfirmed_drafts/hot-spots-groupings.md`](unconfirmed_drafts/hot-spots-groupings.md) | **draft / idea** (2026-10-06); one schema-timing decision before the release (see the draft) | 4b |

Plans 1, 2 and 3 are independent and can proceed in parallel. 1 and 2 set contracts others use,
so they were written first.

## How to continue

Plans 1-4 and WI-0 are implemented (see the table above; each plan file's "Implementation status" is the authoritative record of what exists and how it differs from its text). **Plans 5-8 are drafts**: each now has a "State of the code" section listing what it can build on and which assumptions changed, but their open questions are still unanswered and the user wants them kept as drafts until after the release.
WI-0 follow-ups that still wait for captures from Copilot, Ollama, llama.cpp and vLLM: housekeeping/compaction detectors per agent and more
source parsers. The `compaction_trigger` rule and the legacy leaf-form `json_path` values in the author's DB were reviewed and settled on 2026-10-06 (D24, D25).

1. Read this file, then the plan you are asked to work on, then `AGENTS.md`.
2. If the plan is a **draft**, it is *not* an approved spec: list its "Open questions", ask the user,
   fold the answers in, then move the file from `unconfirmed_drafts/` to `plans/` and update the
   status table above.
3. Verify every code reference in a plan before relying on it; plans record the state on 2026-10-05/06.
4. When a plan is implemented, mark its status here and in the plan, update `SPEC.md`,
   `docs/development.md`, `docs/changelog.md` as relevant.

## Working agreements and practical notes (for agents continuing this work)

How the user works (observed 2026-10-05/06, consistent across all plans):
- **Plan → review → decide → implement.** A plan is written, then *reviewed against the code and real measurements*, open questions are answered (the user answers product/privacy ones, the agent answers technical ones), the plan file is updated, and only then implemented. After implementing, the plan gets an "Implementation status" section that lists exactly what exists, every difference from the plan, what was *not* done and what was *not* verified. **Never leave the roadmap or a plan claiming more (or less) than the code does; the user asked for this explicitly.**
- **The user commits.** Do not commit unless asked. Status lines say "uncommitted" while true; check `git log`/`git status` and correct them (they go stale when the user commits).
- **Provider neutrality (D16):** never justify a decision with the contents of the author's local database (heavily Codex); use it only for sizing, plausibility and fixtures. Agent-specific logic is an optional registered plug-in over a generic baseline.
- **Measure before deciding, on a copy.** Copy the live DB with SQLite's online backup API into the scratchpad (`sqlite3.connect("file:...?mode=ro", uri=True).backup(dest)`); never run migrations, compaction or archive on the live `~/.contextspy/contextspy.db`. Record measured numbers in the plan and label them as samples.
- **One plan file per feature; drafts in `unconfirmed_drafts/`** until their open questions are answered. Update `SPEC.md`, `docs/*`, `docs/changelog.md` with each change; run `pytest`, and for UI changes `cd ui && npm run check` and `make ui`.
- Policies in `AGENTS.md` still apply: analysis in Python/SQL, UI only renders; any `models.py` change needs the `_migrate()`/`migrations.py` step.

Pitfalls found the hard way (each cost real time):
- macOS has no `timeout` command; run long probes in the background writing to a log in the scratchpad and poll the log. A `rm` whose target contains a shell variable is blocked by a safety check (use literal absolute paths; do not rely on cleanup commands inside a larger command, the whole command is dropped, including any heredoc that creates a script).
- **tiktoken is pathologically slow (and can overflow its regex stack) on long runs of one repeated character**; tests that need large text must use prose-like text (`tests/test_session_archive.py: prose()`).
- **SQLAlchemy cannot step `PRAGMA incremental_vacuum`** (it closes the result after one step); use `engine.raw_connection()` and `fetchall()`. `auto_vacuum` must be set *before* `journal_mode=WAL` on a new file.
- SQLite plans: an aggregate `GROUP BY content_hash` over a join can make the planner scan `idx_blocks_content_hash` (15-100x slower); drive from the scope table with `CROSS JOIN`. Cheap-looking per-click lineage calls cost 1-7 s on long sessions (revision check hashes every block row); cache membership (see `db/block_occurrence_service.py`).
- Pysqlite begins transactions lazily at the first DML; to make "measure then delete" consistent, take the write lock first with a harmless write.
- The server refuses to start while a data migration is pending (`contextspy db-upgrade` first); the live database is **still not compacted/archived/upgraded by the agent**, and the author's `blocks.json_path` column already holds 6,794 legacy leaf-form values from an earlier prototype (see `archive/wi0-data-foundation.md` §17).

Where things stand at the end of the 2026-10-06 session: WI-0 and Plans 1, 3a, 3b, 4a, 4b are implemented and committed (not released, not seen in a browser by the author); Plans 5, 6, 7, 8 are drafts/ideas. Open decisions waiting for the user: answers to Plan 5's seven open questions (deliberately deferred until work on that plan starts); a decision on the hot-spots groupings idea (Plan 9: **the user asked to be prompted for it when work on that plan starts**; the schema-timing question before the release is also still open); whether to do something about the 65 s cold conversation analysis (issue #68, draft plan) and the slow request-listing query (issue #69, a small covering-index fix). The user's plan (D23): merge the branch, release for testing, then continue the drafts and UI styling.

## Release readiness (merge of `analysis_revamp`, written 2026-10-06)

What a user upgrading from 0.5.4 (schema 8) experiences, and what has *not* been verified:
- `contextspy start` **refuses to start** until `contextspy db-upgrade` has run (existing gating). The upgrade backs the database up first (`..._backup_v8_to_v10_<UTC>.back`), runs v9 then v10 and prints progress; ~2-3 min per 7k requests / 1.5M blocks on the author's machine, longer on slower disks. v10 finds nothing to do after v9 because v9 now already derives file paths. New databases are created at the current version.
- Behaviour changes to call out in release notes: time-based purge default is now off (`[retention]` values of 0; explicit values keep working with a startup notice); new databases use incremental auto-vacuum; sessions can be archived (one-way); `db-compact` exists.
- Source keys and file paths for requests whose tool-call text was already purged stay generic (`tool:<name>`) / NULL; this is permanent for that data.
- **Not verified:** any new UI in a browser (Present in, purpose chip, File row, Archive modal/badge/notice, Hot spots page); Windows (`db-compact` lock path, incremental vacuum); packaging (Homebrew/.deb/standalone: run `make ui` so `contextspy/_web/` is current, and confirm the new modules `analysis/paths.py`, `analysis/block_hotspots.py`, `db/hotspots_service.py` are picked up); upgrading a database that was captured while another version ran; a fresh install end to end.
- Known limitations to state honestly: the first conversation-scoped view of a very long session can take a minute (cold lineage analysis); hot spots count visible tokens only; relative/absolute spellings of one file are separate rows; Ollama's adapter does not capture tool calls; only Codex `exec`/`js`, `Bash` and structured file tools are parsed for sources/paths.
- **Decision before release (schema timing):** if the invocation grouping in [`hot-spots-groupings.md`](unconfirmed_drafts/hot-spots-groupings.md) might use a persisted call/result link (its option 2, schema v11), adding the column before the first release spares users a second `db-upgrade`; with its read-time option 1 nothing needs deciding.
- Release mechanics (not done): version bump in `pyproject.toml` (currently 0.5.4), rename the changelog's "Unreleased" heading, tag/package as usual. The changelog's Unreleased section was reviewed against the code on 2026-10-06.

## Maintenance notes for the implemented code

Invariants to keep (each has a test; do not "simplify" them away):
- **Classification versioning:** any change to `sources.py`/`purpose.py`/`paths.py` output means bump `analysis/purpose.py: CLASSIFIER_VERSION` and add a data migration that re-derives rows below it; v10 shows the pattern (`migrations._backfill_classification(db, label, json_paths=...)`, shared with v9). Capture-time classification is what matters; backfill is best effort over retained content.
- **Single writers:** `analysis/paths.py: normalize_file_path` is the only function that decides what goes into `blocks.file_path` (privacy/obfuscation hook, D18); `resolve_sources` is the only producer of `source_key`/`file_path`. Never read other tool arguments into stored fields (the secret-argument tests guard this).
- **Hot-spots SQL:** the scope temp table must be the outer loop (`FROM hs_scope s CROSS JOIN blocks b ...`); `tests/test_hotspots.py` asserts the plan for all groupings; `aggregate_select` must not depend on sort/paging/in-context (cache seam, issue #67). The latest occurrence is packed as `position * 2**32 + block id` (block ids must stay below 2**32), run counts come from `GROUP_CONCAT(pos)`.
- **Scope resolution:** `block_occurrence_service.scope_for_session` is the one place that turns (session, conversation) into an ordered request list; positions are indexes in *scope order*, not `session_seq`. `with_conversations=True` builds the conversation membership even for session scope (slow when cold); the membership cache is 60 s per session, keyed by request count, with the lifetime counted from the end of the build.
- **Archive/compaction:** blocks of archived sessions never keep shared content alive; the write lock is taken first (`UPDATE sessions SET name = name`); `auto_vacuum` must be set before `journal_mode=WAL`; `incremental_vacuum` needs a raw DBAPI connection; backups made before a compaction are unaffected by it; `list_backups` orders by the timestamp in the name.
- **Analysis must work without content (D3):** block rows, hashes, token counts, source keys, file paths and labels survive archive; text, previews and raw JSON do not. Hot-spot labels come from stored structure (`analysis/block_hotspots.py: block_label`).
- **Block row ids are request-local** and change if a migration rebuilds a request's blocks, so `?block=` links are not durable across such migrations.
- **Frontend policy:** the UI shows what the API computed. The category labels live in `ContextBar.tsx` (shared by donut, bar and hot spots); block colours in `lib/blockVisuals.ts` (`visualForType`).
- **Test hazards:** tiktoken is pathologically slow on long runs of one repeated character (use prose); pytest runs ~15 s, Vitest ~6 s; `setupTests.ts` stubs `scrollIntoView`.
- **Known performance facts** (samples): cold lineage/conversation membership 65 s (4,032 requests), 7 s (570); `select ... from requests where session_id=?` is slow on unarchived sessions because SQLite walks the large inline body columns (0.38 s for 570 requests); both now have a `perf:` issue and a draft plan: cold lineage analysis [#68](https://github.com/RimantasZ/contextspy/issues/68) ([`perf-cold-lineage-analysis-68.md`](unconfirmed_drafts/perf-cold-lineage-analysis-68.md); profile: ~67% candidate scoring via `diff_contexts`, ~19% the per-call revision hash, ~14% snapshot loading) and the request-listing query [#69](https://github.com/RimantasZ/contextspy/issues/69) ([`perf-session-request-listing-index-69.md`](unconfirmed_drafts/perf-session-request-listing-index-69.md); a covering index took 0.6-0.9 s to ~0 ms on the sample, `context_fidelity` is column 57, after the body columns).

## Inputs for the draft plans (details in each draft's "State of the code")
- **Context tree (5):** needs request-level *turn grouping* (not implemented), a controlled JSON viewer reveal (the viewer was rebuilt: `components/ui/content-viewer/`, `useTreeExpansion`), and per-block fields that now exist (`source_key`, `activity`, `file_path`, `json_path`, link ids). Its conversation scope inherits the cold-membership cost.
- **Compare (6):** `ContextDelta` already exposes `removed`; block ids are request-local; archived content cannot be diffed (D3); `compaction` purpose helps explain lineage breaks.
- **Hints (7):** hot-spots rows already carry the inputs (`run_count`, `in_latest_request`, `dropped`, first/last seen, read vs edit tokens); file-path coverage is partial (8% of blocks in the sample) and spellings split.
- **Similarity (8):** exact-hash grouping is what exists; file version chains need path unification and are only as good as path coverage.

## Cross-cutting open questions

- Verify all adapters (Anthropic, OpenAI chat/responses, Ollama) populate cache fields consistently
  (follow-up, after initial implementation; see D2).
- Definition of "dead weight" for hints (plan 7) needs product input.
