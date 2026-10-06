# Plan 4b: Hot spots — what stays in the context window, and what it costs

Status: **implemented 2026-10-06, committed in `e6c4396` (category filter `4cb4c84`); not released; not seen in a browser (styling remarks from the author are pending). See "Implementation status" at the end.** Reviewed and decided 2026-10-06, re-reviewed after Plan 4a the same day ("Second review"). Depends on [info-panel.md](info-panel.md) (implemented: occurrence semantics, `?block=` deep link, conversation membership), [session-archive.md](session-archive.md) (implemented: analysis must work without content) and, for the *Files* grouping, [file-paths.md](file-paths.md) (Plan 4a). Part of [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md).

## Goal
Show the **top blocks** of a conversation (or whole session) ranked by **total visible tokens** or by **occurrence count**, so the user sees what information is carried through the window repeatedly and what it costs over time. Also rank by **source** (which tool costs the most) and by **file** (which file's contents are carried most).

## Decisions
Carried over: cost = tokens × occurrences, **visible tokens** only (D1, D2, D11); scope **per conversation by default with a switch to the whole session** (D5, same membership as the dashboard and Plan 1); must work on archived/purged data (D3); analysis in Python/SQL, UI only renders; visually match Request detail (D9).

Answers to the draft's open questions (architect, 2026-10-06; the file-path answer is the user's):
1. **Identity across roles:** keep `content_hash`. Measured (author's sample): 4 of ~15,000 hashes occur under more than one block type, so a composite key would change nothing. A row lists `block_types` when a hash spans several; hash-less blocks (hidden reasoning, empty results) cannot be matched and appear as one summary line ("N unidentifiable blocks, T tokens"), not as rows.
2. **Files:** block `attrs` hold only structural flags and tool arguments are not stored, so there was nothing to group by. **Decision (user): store full file paths** (Plan 4a, implemented) and offer a *Files* grouping on top. A *by source* grouping (`source_key`, privacy-safe) is offered as well.
3. **Top-N:** 25 rows by default, "Show more" adds 25 (API `limit` ≤ 100, `offset` ≤ 1,000). The header says how much of the total the visible rows cover ("top 25 = 41% of 53.3M visible tokens").
4. **Lineage breaks:** no marker rows (the list aggregates over time, it is not a timeline). Breaks show up where they matter: each row has `in_latest_request` and `run_count` (a block that disappears and comes back has more than one run), and the **In context** filter offers *All / Still in context / Dropped* (dropped = last seen before the latest request of the scope, which is what a compaction or restart does to a block). The "present in" panel (Plan 1) already shows the exact runs and gaps.

## Review findings (2026-10-06, measured on the author's database; samples, not rules)
- **The obvious SQL is 15-100× slower than it needs to be.** `JOIN scope … GROUP BY content_hash ORDER BY SUM(tokens) LIMIT 25` makes SQLite scan `idx_blocks_content_hash` (it walks the index to avoid a sort): 5.7 s for a whole 564-request session, 14 s for 4,032 requests, and **2 s even for a 50-request conversation**. Forcing the scope as the outer loop (`FROM scope s CROSS JOIN blocks b ON b.request_id = s.id …`) takes **0.13 s / 0.92 s / ~0.01 s**. The plan therefore *requires* the CROSS JOIN form and a test on `EXPLAIN QUERY PLAN` that no full scan of `idx_blocks_content_hash` is used.
- **Fixed overhead dominates naive rankings:** in the sample, the top rows were tool definitions (re-sent every request), compaction items and large tool results; no single block was in ≥ 90% of requests. Category and block-type filters (chips) let the user look at tool results only, etc.; no automatic "overhead" split is attempted.
- **Distinct blocks are many:** 1,457 distinct hashes in a 564-request session, 8,532 in a 4,032-request one; pagination is essential.
- **Row actions need a representative occurrence:** rows carry the *latest* occurrence (`request_id`, `block_id`) so a click can open Request detail with the block selected (`/requests/{id}?block={block_id}`, implemented in Plan 1), where the "Present in" panel shows runs and totals.

## Design

### Endpoint
`GET /api/sessions/{session_id}/hotspots`
Query: `group=block|source|file` (default `block`), `scope=conversation|session` (default `conversation`), `conversation=<group key>` (optional), `sort=total_tokens|occurrences` (default `total_tokens`), `category=`, `block_type=`, `source=`, `in_context=all|current|dropped` (block grouping only), `limit=25` (1-100), `offset=0` (0-1000). 404 for an unknown session; 422 for bad parameters.

Scope: `session` = all requests of the session in session order. `conversation` = the members of the conversation given by `conversation` (a group key from the response's `conversations` list), defaulting to the conversation the session's **latest request** is filed under; auxiliary/unavailable cases fall back to the session with a `scope_note` (same notes as Plan 1). Membership comes from `block_occurrence_service._membership` (cached 60 s); reuse it, do not re-derive.

Response:
```json
{
  "scope": "conversation", "scope_note": null, "group": "block", "sort": "total_tokens",
  "conversations": [{"key": "...", "code": "C1", "request_count": 54, "selected": true}],
  "summary": {
    "scope_request_count": 54, "distinct_blocks": 320, "visible_tokens_total": 5390318,
    "fidelity_counts": {"complete": 40, "partial": 4, "opaque": 10},
    "unidentifiable": {"blocks": 133, "tokens": 432}, "returned_tokens": 2231000, "returned_share_pct": 41.4
  },
  "rows": [ ... ], "total_rows": 320, "has_more": true
}
```
Block row: `key`, `block_type`, `block_types` (only when > 1), `category`, `tool_name`, `source_key`, `activity`, `file_path`, `label`, `preview` (≤ 120 chars, only when content is retained), `content_purged`, `occurrence_count`, `request_count`, `tokens_per_occurrence` (null when occurrences disagree), `total_tokens`, `share_pct`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `run_count`, `latest: {request_id, block_id, session_seq}`.
Source row (rows with no source key are folded under `unknown`): `source_key`, `activity`, `distinct_blocks`, `occurrence_count`, `request_count`, `total_tokens`, `share_pct`, `largest: {…same shape as latest, for the biggest single block}`.
File row: `file_path`, `distinct_versions` (distinct hashes), `occurrence_count`, `request_count`, `total_tokens`, **`result_tokens` (contents read) and `call_tokens` (edits/patches written in the call)**, `share_pct`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `latest: {...}`.
`label`: for tool blocks `source_key`/`tool_name` (+ file); for `other` blocks the provider item type from `attrs` (e.g. compaction); otherwise the block type. It never needs content, so it works after archive.

### Computation (`analysis/block_hotspots.py` + `db/hotspots_service.py`)
- Scope requests → a TEMP table `scope(id PRIMARY KEY, pos INTEGER, seq INTEGER)` (position in the scope's order and the request's `session_seq`, filled in chunks; avoids bound-variable limits). **All aggregation in SQL with the scope as the outer loop** (`CROSS JOIN` form above), input direction only (output blocks reappear as input in the next request, so counting them would double count), `content_hash IS NOT NULL` for the block grouping.
- Block grouping: `GROUP BY b.content_hash` with `COUNT(*)`, `COUNT(DISTINCT b.request_id)`, `SUM(b.token_count)`, `MIN/MAX(scope.pos)`, `MIN/MAX(token_count)`; `ORDER BY` total or occurrences with deterministic ties (`total desc, occurrences desc, hash`); `HAVING` for `in_context` (`MAX(pos) = :last` vs `<`); `LIMIT/OFFSET`. `total_rows` and the summary come from a second cheap aggregate over the same scope.
- For only the returned N hashes (bounded): one query for the latest occurrence per hash, the row's descriptive columns (type, tool, source, file, category, `attrs` provider item type) from that latest block, the other block types, `run_count` (positions of the hash's requests → runs, pure function), and previews (`block_contents` joined for those hashes only).
- Source grouping: `GROUP BY b.source_key` (rows with `source_key IS NULL` are folded under `unknown`), `COUNT(DISTINCT b.content_hash)` for `distinct_blocks`. File grouping: `GROUP BY b.file_path WHERE file_path IS NOT NULL` over `tool_call` and `tool_result` blocks. Both reuse the same scope table.
- No result cache in 4b (postponed, see "Second review" item 1); the single-pass aggregate is the seam where one would go.

### Performance requirements (how the slow-plan finding is addressed)
The finding is a *design requirement*, not just a note: it is enforced by the query shape, a test, and a manual budget check. **Nothing is implemented yet; the numbers above are from prototype SQL on the author's database.**
1. **Query shape:** the scope table is always the outer loop (`FROM scope s CROSS JOIN blocks b ON b.request_id = s.id`; in SQLite `CROSS JOIN` fixes the loop order), and the inner loop uses `idx_blocks_request`. No `GROUP BY b.content_hash` over a join the planner is free to reorder. Every hot-spots query (rows, totals, source/file groupings) is written this way.
2. **Regression test (`tests/test_hotspots.py`):** build a database with enough rows for the planner to care (thousands of blocks across dozens of requests), run `EXPLAIN QUERY PLAN` for each aggregation and assert the driving loop is `SCAN scope`/`SEARCH b USING INDEX idx_blocks_request` and that `idx_blocks_content_hash` is not scanned; also assert the statement count is constant as requests grow. If a future SQLite version changes the plan the test fails instead of the feature silently becoming 15-100× slower.
3. **Bounded second phase:** descriptive columns, latest occurrence, `run_count` and previews are fetched only for the returned rows (≤ 100), never for all distinct blocks.
4. **Budget (manual check on a copy of a real database before release, numbers recorded in this plan):** whole 4,000-request session ≤ ~1.5 s, whole 500-request session ≤ ~0.3 s, a 50-request conversation ≤ ~50 ms, for the top-25 page and the summary together. Exceeding it after the single-pass aggregate (second review) means revisiting the query before shipping; the postponed cache only helps repeat calls.
5. **Fallback if the CROSS JOIN form ever proves insufficient:** `INDEXED BY idx_blocks_request` on the inner table, or materialising the scoped rows into a second temp table once per request and aggregating from it (variant D in the measurement: 0.02-1.3 s).

### Second review (2026-10-06, after Plan 4a; measured on a copy of the author's database, samples only)
What changed or was found, and what the plan now says:
1. **The budget is marginally missed on the largest session with two passes.** On the copy (4,032 requests, 782k input blocks, 8,532 distinct hashes) the top-25 aggregate takes 1.17 s, the summary aggregate another 0.53 s, files 0.26 s, sources 0.77 s; the 570-request session 0.50/0.23/0.19/0.55 s. The plan shape is as intended (`SCAN scope`, `SEARCH b USING INDEX idx_blocks_request`, temp B-tree for group by; `idx_blocks_content_hash` unused). Page plus summary as two passes (~1.7 s) exceeds the ~1.5 s budget. **Decision:** run the grouping **once** into a TEMP table `agg` (one row per hash/file/source, ≤ ~10⁴ rows) and derive `rows`, `total_rows`, `summary`, `in_context` filtering, sorting and pagination from `agg` (milliseconds). Implement the grouping as one function `aggregate(scope, group, filters)` that does not depend on `sort`, `limit`, `offset` or `in_context`. **A cache is deliberately not part of 4b** (user, 2026-10-06): it is postponed in [postponed/hot-spots-cache.md](postponed/hot-spots-cache.md) and tracked as GitHub issue [#67](https://github.com/RimantasZ/contextspy/issues/67); it wraps that function and needs no change to this plan. Without it, "Show more" and the sort toggle repeat the ~1.2 s pass on the largest session (0.5 s on a 570-request one); the first call fits the budget.
2. **File rows must separate reads from edits.** With paths now stored on both the tool call and its result, a file's tokens mix *contents read* (results) and *patches/edits written* (calls). In the sample, calls carry 29% of path-tagged tokens, and in the 570-request session the top file was 99.5% `apply_patch` call text. Rows therefore carry `result_tokens` and `call_tokens` (total = sum), shown as "read X · edited Y"; sorting stays on the total. Wording in the UI and FAQ: "tokens attributed to the file", never "file contents" alone.
3. **Relative and absolute spellings split one file into several rows** (measured, e.g. `contextspy/db/crud.py` and an absolute path under another checkout). Still out of scope (needs a working directory the proxy does not see); the FAQ says so. Rows show the path exactly as stored.
4. **Source grouping is only as fine as the stored keys.** Where call text was purged before the keys were derived (the largest sample session), Codex calls appear as `tool:exec` / `tool:js` rather than per program; this is a property of the data, not a bug, and the Sources view should not hide it.
5. **Code adjustments to the plan:** `block_occurrence_service._scope_requests` takes a *request* and derives the conversation from it. Hot spots needs scope by *session + conversation key* (default: the conversation of the latest request), so factor out `scope_for_session(db, session_id, scope, conversation_key)` that both Plan 1 and hot spots call; keep `_membership` as the single source of membership. `_Membership` has codes per request but no labels; the `conversations` list therefore carries `key`, `code`, `request_count` only (no label).
6. **UI adjustment:** `SessionDetail` already reads `?conversation=` for the Conversations view (it also forces the layout), so the hot-spots view must read it itself and `view=hotspots` must not reuse `conversationLayout`; switching views clears `mode`/`layout`/`conversation` as the existing control does for the others.
7. **Unchanged and re-confirmed:** hash identity, 25 rows + show more, in-context filter, input-direction only, bounded second phase, no similarity grouping. Hash-less input blocks were 28% of blocks in the sample but 0 tokens (their token counts are unknown or zero), so the "unidentifiable" footnote stays small.
8. **Tests to add:** file rows' `result_tokens + call_tokens == total_tokens`, `scope_for_session` equivalence with Plan 1's scope for the same request.
Performance budget revision (to verify after implementation on a copy): every call ≤ ~1.5 s for 4,000 requests (one aggregate pass + bounded second phase); repeat-call latency (paging, sort) is the postponed cache's job.

### UI (`ui/src/pages/SessionDetail.tsx`, new `components/hotspots/*`)
- A third option in the existing **Session view** control: *Summary / Conversations / Hot spots* (`?view=hotspots`); the Conversations view gets a "Hot spots" link per conversation (`?view=hotspots&conversation=<key>`).
- Controls (one compact toolbar, same visual language as the Request detail toolbar): scope select (conversations by code + "Whole session"), group toggle (*Blocks / Sources / Files*), sort toggle (*Total tokens / Occurrences*), filter chips (category, block type; *In context* for blocks).
- Rows are lightweight: icon (existing `blockVisuals`), label, small muted meta (`×occurrences · requests · tokens each`), right-aligned total with a thin share bar; `reappears` and `dropped` as small badges. Click opens Request detail on the latest occurrence with the block selected. "Show more" button; header sentence with coverage; an "N unidentifiable blocks" footnote; partial/opaque badge counts with a tooltip (visible-token wording, D11).
- Empty/loading/error states; archived sessions show the same rows (previews absent).
- Hooks: `useSessionHotspots(sessionId, params)` with `placeholderData` that keeps the previous rows while parameters change.

## Tests
Backend (`tests/test_hotspots.py`):
1. Counts/totals for repeated hashes; within-request repeats raise `occurrence_count` not `request_count`; `tokens_per_occurrence` null when they disagree.
2. Both sorts, tie-break determinism, limit/offset/`has_more`, `total_rows`, bad parameters → 422, unknown session → 404.
3. Scope: conversation excludes interleaved other-conversation requests (fork fixture from `test_block_occurrences.py`), shared-history root included in both, auxiliary fallback with `scope_note`, default conversation = the latest request's, session scope.
4. `in_context`: `current` vs `dropped` with a block that disappears; `run_count` for a block that goes away and returns.
5. Filters (category, block type, source); hash-less blocks only in `summary.unidentifiable`; output blocks never counted.
6. Source and file grouping (file rows from fixtures with `file_path`; `distinct_versions`).
7. Archived and purged data: identical numbers, `preview` null, `content_purged` true.
8. **Plan shape:** `EXPLAIN QUERY PLAN` of the aggregation does not list a scan of `idx_blocks_content_hash` as its driving loop; query count is constant regardless of the number of requests.
9. Representative `latest` is the highest `session_seq` occurrence and belongs to that request.
10. Real-data timing check on a copy (document numbers in the plan).
Frontend (Vitest): toolbar state and URL params, group/sort/filter changes refetch, "Show more", row click navigates with `?block=`, states (loading/empty/error/partial fidelity badges), the Conversations-view link, archived rendering. `npm run check`, `make ui`.

## Docs
`SPEC.md` (endpoint), `docs/changelog.md`, `docs/faq.md` ("what do hot spots count?"), `docs/development.md` (the CROSS JOIN requirement and why).

## Not in scope
Cache reporting (D2), money (D1), optimisation hints (Plan 7, which reads these rows), output-block rankings, unifying relative/absolute file spellings, an automatic overhead/accumulation split, and **similarity grouping** (changed-and-reloaded blocks, near-duplicates, version timelines): grouping here is by **exact content hash** only; the similarity idea is parked for v2 in [unconfirmed_drafts/similarity-grouping.md](unconfirmed_drafts/similarity-grouping.md). The *Files* grouping already ties the versions of one file together.

## Open questions
None blocking. Further groupings (block types, commands, invocations) are a separate idea: [unconfirmed_drafts/hot-spots-groupings.md](unconfirmed_drafts/hot-spots-groupings.md).

## Implementation status (2026-10-06)

Implemented: `GET /api/sessions/{id}/hotspots` (all three groupings, both sorts, scope/conversation selection, filters, paging, `in_context`), `analysis/block_hotspots.py` (pure helpers), `db/hotspots_service.py`, `block_occurrence_service.scope_for_session` (shared with Plan 1), the **Hot spots** view in the session page (`components/hotspots/HotSpots.tsx`, `?view=hotspots`), a per-conversation "Hot spots" link in the Conversations view, docs (`SPEC.md`, `development.md`, `changelog.md`, `faq.md`). Backend 685 tests passed, frontend 199 passed (after the category filter), `npm run check` clean. **Not seen in a browser; not released; the live database has not been upgraded** (the feature needs schema v10 for the Files grouping only).

Differences from the plan:
- **No cache** (postponed, [postponed/hot-spots-cache.md](postponed/hot-spots-cache.md), issue #67). The grouping is one function, `aggregate_select`, independent of sort/paging/in-context, as required.
- **Latest occurrence and run counts come out of the same pass**: the latest block is found by maximising `position * 2**32 + block id` and run counts from a `GROUP_CONCAT` of positions. The first design (a second lookup of the returned hashes' positions through `idx_blocks_content_hash`) made a 100-row page cost ~3 s on the largest sample because it walks the hash across all sessions.
- **`summary` has no `distinct_blocks`** (`total_rows` carries it); hash-less blocks are the NULL group of the same aggregate, so `unidentifiable` and the totals cost no extra pass. File and source shares are of the whole filtered scope (the same trick for blocks without a file).
- **Filters**: the API has `category`, `block_type`, `source`; the UI offers *category* (the same eight categories and labels as the context bar and donut, taken from `ContextBar.tsx`), *block type*, *In context* and a *source* chip (reached from a Sources row's "Blocks" button). Category is a classifier heuristic (e.g. `file_contents` is a tool result recognised as file text), so it also finds file-like results where no path was captured; shares are relative to the filtered scope. (An earlier draft of this status omitted the category select with the wrong reason that the UI did not know the vocabulary; it does, and the select was added afterwards.) `in_context` with a non-block grouping is a 422.
- **Show more** is offset paging of 25 rows (infinite query), capped at offset 1,000 (then a note says to narrow the scope).
- `conversations` in the response carries `key`, `code`, `request_count`, `selected` (no label), and is always built, so the first call of a session pays for the conversation membership (below).
- **Side fix:** the membership cache lifetime was counted from the *start* of the build, so a build slower than 60 s was never reused; it now counts from the end (test added). Also `scope_for_session(..., with_conversations=False)` keeps Plan 1's session scope from building membership.

Measured on a copy of the author's database (samples, 2026-10-06; warm OS cache; one machine, noisy +/- 50%):
- Aggregate + page, **4,032-request session** (782k input blocks, 8,532 distinct hashes): ~1.1 s steady for blocks, ~1.0 s for files and sources (outliers up to 2.6 s). **Within the ~1.5 s budget, marginally.** 100-row pages cost the same.
- **570- and 564-request sessions:** 0.2-0.5 s. A 39-request conversation inside the 570-request session: ~0.3 s, **above the ~50 ms target**: 0.38 s of it is the existing query listing the session's requests (`select id, session_seq, timestamp, context_fidelity`), which is slow for unarchived sessions because SQLite walks each row's large inline body columns to reach later columns. A narrow covering index or moving the bodies out of `requests` would fix it; not done here.
- **Cold conversation membership (the existing lineage analysis): 65 s on the 4,032-request session and 7 s on a 570-request one, ~5 s warm-graph rebuild, 0.08 s when cached.** Hot spots inherit this because the conversation selector and the default conversation need it. The same cost already applies to the Conversations view and the block "Present in" panel. Consider a cheaper way to resolve conversations (or computing it ahead of time) before calling this feature fast on very long sessions: tracked as issue [#68](https://github.com/RimantasZ/contextspy/issues/68) ([plan](unconfirmed_drafts/perf-cold-lineage-analysis-68.md)); the request-listing part is issue [#69](https://github.com/RimantasZ/contextspy/issues/69) ([plan](unconfirmed_drafts/perf-session-request-listing-index-69.md)).
- Plan shape confirmed on the real database: `SCAN s`, `SEARCH b USING INDEX idx_blocks_request`, temp B-tree for the group; `idx_blocks_content_hash` is not used.

Not done / not verified: browser check; first-call time as the user would experience it on a long, never-opened session (dominated by the membership above); fidelity badge wording with real partial/opaque data; the Files grouping on a database with paths from non-Codex agents.
