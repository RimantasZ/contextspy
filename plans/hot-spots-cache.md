# Plan: cache for the hot-spots aggregate

Status: **implemented, uncommitted** (un-postponed and implemented 2026-11-11); tracked in https://github.com/RimantasZ/contextspy/issues/67. Split out of [archive/hot-spots.md](archive/hot-spots.md) (Plan 4b), which was implemented without it. Revised and measured 2026-11-11 against the implemented `db/hotspots_service.py`.

## Why it exists
Measured on a copy of the author's database (sample, 2026-10-06): grouping a 4,032-request session (782k input blocks, 8,532 distinct hashes) takes about 1.2 s, a 570-request session about 0.5 s. Without a cache every request to the endpoint (first page, "Show more", sort toggle, in-context filter) repeats that pass.

## Current implementation (what the cache has to fit)
`get_session_hotspots` resolves the scope, loads it into TEMP `hs_scope`, runs `aggregate_select(group, category, block_type, source)` into TEMP `hs_agg`, then derives totals, `total_rows`, the page (SQL `WHERE`/`ORDER BY`/`LIMIT`) and the descriptive columns (`_describe`, bounded by the page) from it, and drops both temp tables. `aggregate_select` is the seam: it does not depend on `sort`, `limit`, `offset` or `in_context`.

## Design
**Split the function in two.**
1. `_aggregate(db, resolved scope, group, filters)` returns plain Python data: the aggregate rows (one dict/tuple per `k`) and the values derived from the whole table (`total`, `occurrences_total`, `unidentifiable`), plus the scope-level values that do not depend on the filters (fidelity counts, `input_totals` for session and conversation). This is the only part that touches `hs_scope`/`hs_agg`; it still runs the single grouping statement and still keeps the scope table as outer loop (docs/development.md "Hot spots queries").
2. `_page(aggregate, sort, in_context, limit, offset, last_position)` is pure Python: filters `k IS NOT NULL` and `latest_key // POSITION_SHIFT` (`=` for current, `<` for dropped), sorts, counts `total_rows`, slices. `_describe` and row building stay as they are and run on the page only.

The cache stores the result of step 1; every call runs step 2.

**Ordering must match SQL exactly.** `total DESC, occ DESC, k` and `occ DESC, total DESC, k`, with `k` ascending. Compare `k` as UTF-8 bytes or document why code-point order is equivalent; this is the main correctness risk.

**Store `run_count`, not `poss`.** For the `block` group the temp table carries `GROUP_CONCAT(s.pos)` (every occurrence's position, a few MB for the largest session). The run count is a function of the group alone, so compute it when the entry is built (`count_runs`) and cache only the number.

**Cache shape.** In-process, like `db/block_occurrence_service._membership` (`Lock`, `time.monotonic()` expiry set after the build, `clear_hotspots_cache()` for tests):
- Key: (`db.get_bind()`, session id, session request count, requested scope, conversation key, group, category, block_type, source). `sort`, `limit`, `offset`, `in_context` are deliberately not in the key.
- TTL 60 s, at most 8 entries, least-recently-expiring entry evicted at the limit.
- **Not** one entry per key family: switching the group tab or a filter must not evict the previous entry. Instead, when an entry is stored, delete entries of the same (database, session id) whose request count differs (they are stale).
- Memory: ~10⁴ rows per entry, small; 8 entries is a bounded few MB.

**Key needs the request count first.** Use a cheap `COUNT(*)` of the session's requests before resolving the scope, as `_membership` does. The resolved scope (conversation vs. session fallback, `scope_note`, `conversations`) is deterministic from the key, so cache it with the aggregate or re-resolve it cheaply from the cached membership; decide after measuring (below).

**Invalidation.** Immediately when the session's request count changes (new key). In-place edits (lineage changes, `db-upgrade`, re-analysis, `session archive` changing `source_key`/`file_path`/`category`) can lag by the TTL; say so in docs/development.md.

## Step 0: measurements (2026-11-11, copy of the author's database, 4,032-request and 564-request sessions)
Per call, warm membership cache, `group=block`:

| Phase | 4,032 req | 564 req |
|---|---|---|
| `scope_for_session` (warm membership) | 130-350 ms | 17 ms |
| `_input_totals` (once; called for session and conversation scope) | 24 ms | 2 ms |
| `_load_scope` + grouping into `hs_agg` | 0.8-2.6 s (block 1.9-2.6 s, source/file ~0.8 s) | ~0.12-0.15 s |
| materialising `hs_agg` into Python (8,533 rows) | 13 ms | 2 ms |
| **full `get_session_hotspots` call** | **~0.9 s** | **~0.17 s** |
| membership **cold** build (first call, then every 60 s TTL expiry) | **17-40 s** | 4.8 s |

Conclusions:
- The grouping is ~85-90% of a call; materialising the aggregate in Python is negligible (13 ms), so the "split into `_aggregate` / `_page`" design costs nothing.
- A hit that still resolves the scope and runs `_input_totals` would take ~150-400 ms on the largest session, **not** the 50 ms target. Scope resolution (`scope_for_session`: loads and sorts all request rows) must be inside the cached value, keyed so that it is not re-run: either cache `SessionScope` + `input_totals` + fidelity under the same key, or key the hit check on a cheap `COUNT(*)` only. With that, a hit is a Python sort/slice of ≤ ~10⁴ rows plus `_describe` on ≤ 100 ids.
- Separate and larger than this plan: the cold membership build (17-40 s) is paid on the first call and again whenever the 60 s TTL lapses. That is issue #68 (cold lineage analysis), not part of this cache, but the hot-spots cache TTL should not be shorter than, nor hide, that cost: if the hot-spots entry outlives the membership entry, a hit must not touch the membership at all.

## Tests (`tests/test_hotspots.py`, with `clear_hotspots_cache()` in a fixture)
- Hit: second call with a different `sort`, `offset`/`limit` or `in_context` does not run the grouping (count `aggregate_select` executions or patch `_aggregate`).
- Miss: a new request in the session, a different category/block_type/source filter, group, scope or conversation key.
- Stale entries for the same session are dropped; the entry limit evicts at 8.
- TTL expiry (patch `time.monotonic`).
- Equivalence: for every group × sort × in_context (and a paged sequence), the cached and uncached responses are identical, including tie order and `unidentifiable`/summary values.
- The existing query-plan test (scope table as outer loop) keeps passing.

## Documentation
Update docs/development.md "Hot spots queries" (cache, TTL, key, staleness) and the AGENTS.md pointer if it describes the service; mark D19 in `ANALYSIS_ROADMAP.md` as superseded when this ships.

## Result
Implemented in `db/hotspots_service.py` (`_build_aggregate`, `_aggregate`, `_page`, `clear_hotspots_cache`); the cached value also holds the resolved scope, fidelity counts and input totals, and the key's request count comes from a cheap `COUNT(*)`. On the 4,032-request copy: first call per key unchanged (~0.9 s warm membership), page / sort / in-context hits 2-8 ms (target was ≤ 50 ms). Tests in `tests/test_hotspots.py` ("cache" section); full suite 772 passed. The cold membership build (17-45 s) is untouched (issue #68).

## Open
Whether a persisted aggregate is worth it if the first-call time (~1.5 s on a 4,000-request session) is not acceptable in the UI; that is a separate, bigger idea.
