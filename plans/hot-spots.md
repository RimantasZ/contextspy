# Plan 4b: Hot spots — what stays in the context window, and what it costs

Status: **reviewed and decided 2026-10-06; not started.** Depends on [info-panel.md](info-panel.md) (implemented: occurrence semantics, `?block=` deep link, conversation membership), [session-archive.md](session-archive.md) (implemented: analysis must work without content) and, for the *Files* grouping, [file-paths.md](file-paths.md) (Plan 4a). Part of [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md).

## Goal
Show the **top blocks** of a conversation (or whole session) ranked by **total visible tokens** or by **occurrence count**, so the user sees what information is carried through the window repeatedly and what it costs over time. Also rank by **source** (which tool costs the most) and by **file** (which file's contents are carried most).

## Decisions
Carried over: cost = tokens × occurrences, **visible tokens** only (D1, D2, D11); scope **per conversation by default with a switch to the whole session** (D5, same membership as the dashboard and Plan 1); must work on archived/purged data (D3); analysis in Python/SQL, UI only renders; visually match Request detail (D9).

Answers to the draft's open questions (architect, 2026-10-06; the file-path answer is the user's):
1. **Identity across roles:** keep `content_hash`. Measured (author's sample): 4 of ~15,000 hashes occur under more than one block type, so a composite key would change nothing. A row lists `block_types` when a hash spans several; hash-less blocks (hidden reasoning, empty results) cannot be matched and appear as one summary line ("N unidentifiable blocks, T tokens"), not as rows.
2. **Files:** block `attrs` hold only structural flags and tool arguments are not stored, so there was nothing to group by. **Decision (user): store full file paths** (Plan 4a) and offer a *Files* grouping on top. A *by source* grouping (`source_key`, privacy-safe) is offered as well.
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
  "conversations": [{"key": "...", "code": "C1", "label": "...", "request_count": 54, "selected": true}],
  "summary": {
    "scope_request_count": 54, "distinct_blocks": 320, "visible_tokens_total": 5390318,
    "fidelity_counts": {"complete": 40, "partial": 4, "opaque": 10},
    "unidentifiable": {"blocks": 133, "tokens": 432}, "returned_tokens": 2231000, "returned_share_pct": 41.4
  },
  "rows": [ ... ], "total_rows": 320, "has_more": true
}
```
Block row: `key`, `block_type`, `block_types` (only when > 1), `category`, `tool_name`, `source_key`, `activity`, `file_path`, `label`, `preview` (≤ 120 chars, only when content is retained), `content_purged`, `occurrence_count`, `request_count`, `tokens_per_occurrence` (null when occurrences disagree), `total_tokens`, `share_pct`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `run_count`, `latest: {request_id, block_id, session_seq}`.
Source row: `source_key`, `activity`, `distinct_blocks`, `occurrence_count`, `request_count`, `total_tokens`, `share_pct`, `largest: {…same shape as latest, for the biggest single block}`.
File row: `file_path`, `distinct_versions` (distinct hashes), `occurrence_count`, `request_count`, `total_tokens`, `share_pct`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `latest: {...}`.
`label`: for tool blocks `source_key`/`tool_name` (+ file); for `other` blocks the provider item type from `attrs` (e.g. compaction); otherwise the block type. It never needs content, so it works after archive.

### Computation (`analysis/block_hotspots.py` + `db/hotspots_service.py`)
- Scope requests → a TEMP table `scope(id PRIMARY KEY, pos INTEGER)` (position in the scope's order, filled in chunks; avoids bound-variable limits). **All aggregation in SQL with the scope as the outer loop** (`CROSS JOIN` form above), input direction only (output blocks reappear as input in the next request, so counting them would double count), `content_hash IS NOT NULL` for the block grouping.
- Block grouping: `GROUP BY b.content_hash` with `COUNT(*)`, `COUNT(DISTINCT b.request_id)`, `SUM(b.token_count)`, `MIN/MAX(scope.pos)`, `MIN/MAX(token_count)`; `ORDER BY` total or occurrences with deterministic ties (`total desc, occurrences desc, hash`); `HAVING` for `in_context` (`MAX(pos) = :last` vs `<`); `LIMIT/OFFSET`. `total_rows` and the summary come from a second cheap aggregate over the same scope.
- For only the returned N hashes (bounded): one query for the latest occurrence per hash, the row's descriptive columns (type, tool, source, file, category, `attrs` provider item type) from that latest block, the other block types, `run_count` (positions of the hash's requests → runs, pure function), and previews (`block_contents` joined for those hashes only).
- Source grouping: `GROUP BY b.source_key` (rows with `source_key IS NULL` are folded under `unknown`), `COUNT(DISTINCT b.content_hash)` for `distinct_blocks`. File grouping: `GROUP BY b.file_path WHERE file_path IS NOT NULL` over `tool_call` and `tool_result` blocks. Both reuse the same scope table.
- No result cache. Expected: ≈ 0.1-1 s for a whole large session, ≈ 10 ms for a conversation; revisit only if the first measurements on a real database say otherwise.

### Performance requirements (how the slow-plan finding is addressed)
The finding is a *design requirement*, not just a note: it is enforced by the query shape, a test, and a manual budget check. **Nothing is implemented yet; the numbers above are from prototype SQL on the author's database.**
1. **Query shape:** the scope table is always the outer loop (`FROM scope s CROSS JOIN blocks b ON b.request_id = s.id`; in SQLite `CROSS JOIN` fixes the loop order), and the inner loop uses `idx_blocks_request`. No `GROUP BY b.content_hash` over a join the planner is free to reorder. Every hot-spots query (rows, totals, source/file groupings) is written this way.
2. **Regression test (`tests/test_hotspots.py`):** build a database with enough rows for the planner to care (thousands of blocks across dozens of requests), run `EXPLAIN QUERY PLAN` for each aggregation and assert the driving loop is `SCAN scope`/`SEARCH b USING INDEX idx_blocks_request` and that `idx_blocks_content_hash` is not scanned; also assert the statement count is constant as requests grow. If a future SQLite version changes the plan the test fails instead of the feature silently becoming 15-100× slower.
3. **Bounded second phase:** descriptive columns, latest occurrence, `run_count` and previews are fetched only for the returned rows (≤ 100), never for all distinct blocks.
4. **Budget (manual check on a copy of a real database before release, numbers recorded in this plan):** whole 4,000-request session ≤ ~1.5 s, whole 500-request session ≤ ~0.3 s, a 50-request conversation ≤ ~50 ms, for the top-25 page and the summary together. Exceeding the budget means adding the cache (keyed like the membership cache) before shipping.
5. **Fallback if the CROSS JOIN form ever proves insufficient:** `INDEXED BY idx_blocks_request` on the inner table, or materialising the scoped rows into a second temp table once per request and aggregating from it (variant D in the measurement: 0.02-1.3 s).

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
None blocking.
