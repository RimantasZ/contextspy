> **Superseded in part:** the layout described below was reworked, see [hot-spots-layout.md](hot-spots-layout.md).

# Hand-off: Hot spots page layout work (written 2026-10-06, before a context compaction)

Read this first when picking up the **layout/styling of the Hot spots page**. It is working notes, not a plan; the plan and its implementation record are [hot-spots.md](hot-spots.md), the overall state is [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md) ("Maintenance notes", "Release readiness").

## What you are doing and the ground rules
- The author will give **styling remarks** for the Hot spots page; the page is functionally complete and committed (`e6c4396`, category filter `4cb4c84`) but **the author's first look at it in a browser produced the remarks**, so treat the current look as a first draft. Ask for / wait for the remarks; do not invent a redesign.
- **D9:** tree/compare/hot-spots pages must visually match Request detail (`pages/RequestDetail.tsx`: `.page-shell`, `.panel`, `.surface`, `--border`/`--text-muted` tokens, `app-field`, `app-button`, `app-badge`). Reuse tokens and classes from `ui/src/index.css`; no new design system, no new colours without need (light and dark themes exist: `:root` and the dark block around line 133).
- **Analysis stays in Python** (AGENTS.md policy): layout work is presentation only. Do not change the API, the SQL (`db/hotspots_service.py`) or the response shape for styling reasons; if a layout needs a new field, treat it as an API change with tests and a docs update.
- Workflow: `make dev-backend` + `make dev-ui` while iterating (Vite on :5174 proxies to :5173); before finishing run `cd ui && npm run check` (lint 0 errors, tsc, Vitest, build) and `make ui` so `contextspy/_web/` is current. Backend untouched means `pytest` need not change (685 pass).
- The user commits; do not commit unless asked. Update status lines honestly (roadmap rule).

## Where things are
| Piece | File |
|---|---|
| The whole page (toolbar, summary, rows, show more, states) | `ui/src/components/hotspots/HotSpots.tsx` (280 lines): `HotSpots`, `RowShell`, `BlockRow`, `SourceRow`, `FileRow`, `Summary`, `ShareBar`, `Badge`, `TypeIcon`, `ActivityIcon` |
| Tests (Vitest/RTL, fetch mocked) | `ui/src/components/hotspots/HotSpots.test.tsx` (12 tests) |
| Session page wiring: third view in the "Session view" control, `?view=hotspots`, clears hot-spot params when leaving | `ui/src/pages/SessionDetail.tsx` (+ `SessionDetail.test.tsx`) |
| Link from each conversation to its hot spots | `ui/src/components/SessionConversationSequences.tsx` (`meta` of `ConversationGroupRow`) |
| API types and hook (`useSessionHotspots`, infinite query, 25 rows/page, offset cap 1000) | `ui/src/api/client.ts` (`SessionHotspots`, `BlockHotspotRow`, `SourceHotspotRow`, `FileHotspotRow`), `ui/src/api/hooks.ts` |
| Colours/labels reused | `ui/src/lib/blockVisuals.ts` (`BLOCK_VISUALS`, `visualForType`, `FILTERABLE_BLOCK_TYPES`), `ui/src/components/ContextBar.tsx` (`CATEGORY_LABELS`, `CATEGORY_ORDER`) |
| Shared controls | `ui/src/components/ui/SegmentedControl.tsx` |

## What the page does today (so layout changes do not lose behaviour)
- **Toolbar** (a `.panel`): *Scope* select (conversations as `C1 · 54 requests` plus *Whole session*); *Group by* segmented control (Blocks / Sources / Files); *Sort by* (Total tokens / Occurrences). Second row, **blocks only**: *Category* select (the eight dashboard categories), *Block type* select, *In context* segmented (All / Still in context / Dropped), and a removable *Source: x* chip when filtering by source.
- **URL state** (owned by the page; names exported as `HOTSPOT_URL_PARAMS`): `view=hotspots`, `group`, `scope=session`, `conversation=<group key>`, `sort`, `category`, `block_type`, `source`, `in_context`. Defaults are omitted from the URL. Switching the session view clears them all. Keep these names (deep links come from the Conversations view).
- **Summary line:** "Top N of M blocks = X% of T visible tokens · R requests · 4 partial, 10 opaque" (partial/opaque tooltip explains visible-token wording, D11), plus an "N unidentifiable blocks, T tokens, not listed" footnote (blocks grouping only), plus a scope-fallback note (`auxiliary_request`, `conversation_unavailable`).
- **Rows** (`RowShell`): icon, label (truncate), small meta line, badges, right-aligned total tokens with "x% of visible tokens" and a thin share bar. Block rows: meta = `×occurrences · requests · tokens each (or "sizes differ") · #first–#last` and the content preview when stored; badges *reappears* (`run_count > 1`), *dropped* (not in the latest request), *N types* (`block_types`). Source rows: activity, distinct blocks, ×occ, requests; a **Blocks** button re-filters the block view by that source. File rows: `read X · edited Y · N versions · ×occ · requests`, *dropped* badge.
- **Click** on a row opens `/requests/{latest.request_id}?block={block_id}` (sources: the largest block). Rows are `button`s with descriptive `aria-label`s.
- **Show more** (infinite query), "Showing the top N; narrow the scope" note at the offset cap, loading text "Analysing the context…", error `role="alert"`, empty "No blocks here.", dim to 60% opacity while refetching (previous rows stay visible).
- Archived sessions render the same rows, previews absent (`content_purged`).

## Things in the current layout the author may well dislike (my observations, not their remarks; verify with them)
- Every row repeats "x% of visible tokens" under the number; the header sentence already gives the coverage.
- The meta line is long (counts, seq range, then the preview) and truncates; the preview (where the arguments of a command are visible) is easy to miss.
- Icons are single letters in coloured squares (block types) / activity initials; no real icon set yet (the tree draft wants one set mapped by type/purpose/source).
- No column headers; sort/group controls are above, results are a plain list; the toolbar is two rows of mixed controls (selects + segmented controls).
- Labels such as `tool:Read result · /abs/path` can be long; no middle-truncation of paths.
- No virtualisation (fine at 25-1,000 rows; revisit only if "show more" gets slow).
- Large numbers are plain `toLocaleString()`; no compact units.
- Mobile/narrow widths were not checked.

## Behaviour pinned by tests (change the test with the change, deliberately)
Strings/roles used by `HotSpots.test.tsx` and `SessionDetail.test.tsx`: group buttons *Blocks / Sources / Files* (group "Group by"); sort buttons *Total tokens / Occurrences*; selects labelled *Scope*, *Category*, *Block type*; segmented *All / Still in context / Dropped*; row buttons named `Open the latest request carrying <label>`, `Open the largest block of <source>`, `Open the latest request that touched <path>`; `Show the blocks of <source>`; `Remove the source filter <source>`; *Show more*; texts "Analysing the context…", "No blocks here", "Top N of M blocks", "reappears", "dropped", "unidentifiable block(s)"; the session view button *Hot spots* and the conversation link `Hot spots of <group label>` (`href` `/sessions/<id>?view=hotspots&conversation=<key>`).

## What the API gives a row (do not assume more)
Block row: `key`, `block_type`, `block_types?`, `category`, `tool_name`, `source_key`, `activity`, `file_path`, `label`, `preview` (<=120 chars, null when purged), `content_purged`, `occurrence_count`, `request_count`, `tokens_per_occurrence` (null when sizes differ), `total_tokens`, `share_pct`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `run_count`, `latest {request_id, block_id, session_seq}`. Source row: `source_key`, `activity`, `distinct_blocks`, counts, `total_tokens`, `share_pct`, `largest {…, token_count, label}`. File row: `file_path`, `distinct_versions`, `result_tokens`, `call_tokens`, counts, `share_pct`, first/last seen, `in_latest_request`, `latest`. Response: `scope`, `requested_scope`, `scope_note`, `conversations [{key, code, request_count, selected}]` (no label), `summary {scope_request_count, visible_tokens_total, fidelity_counts, unidentifiable, returned_tokens, returned_share_pct}`, `rows`, `total_rows`, `has_more`. Full contract: SPEC.md (API table).

## Known performance facts that affect the page
First conversation-scoped load of a very long session pays the cold lineage analysis (65 s on a 4,032-request sample; issue #68), so the loading state must stay honest (a spinner/skeleton, not a frozen page); once cached the ranking takes ~1.1 s, and "Show more" repeats that pass (no cache yet, issue #67). Do not trigger extra requests per row.

## Related open items (not layout, do not do them here unless asked)
- GitHub issues: **#67** hot-spots aggregate cache (postponed), **#68** cold lineage analysis, **#69** covering index for the request-listing query, **#70** (`feat:`) more groupings (block types / commands / invocations). Draft plans: `plans/postponed/hot-spots-cache.md`, `plans/unconfirmed_drafts/perf-*.md`, `plans/unconfirmed_drafts/hot-spots-groupings.md`.
- **Prompt the user** when the groupings idea comes up: they were not convinced that "invocations" (call + result as one unit) mostly duplicates Blocks and asked to be asked (see the draft's reminder). A group-selector with more options (Types, Commands, Invocations) would affect the toolbar layout, so mention it when redesigning the group control.
- Release: merge `analysis_revamp`, produce a release for testing, then drafts 5-8 (context tree needs the styling decisions made here: D9). Release checklist and schema-timing question: roadmap "Release readiness".
- Plan 5's seven open questions are deferred until that plan starts; question 7 is about sharing this page's toolbar with the tree.

## State of the repo at the time of writing
Branch `analysis_revamp`; everything implemented is committed (schema v10, 685 backend and 199 frontend tests green at the last run, `npm run check` clean). Latest commits: `b2a0f1f` grouping plan, `c722da0` plans update, `4cb4c84` category filter, `e6c4396` hot spots. The author has not yet released; the author's live database was never upgraded/compacted/archived by an agent (work on scratch copies only, in the session scratchpad).
