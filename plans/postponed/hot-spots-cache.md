# Postponed: cache for the hot-spots aggregate

Status: **postponed 2026-10-06** (user decision); tracked in https://github.com/RimantasZ/contextspy/issues/67. Split out of [../hot-spots.md](../hot-spots.md) (Plan 4b) during its second review. Not required for 4b; add when paging or sort changes feel slow on large sessions.

## Why it exists
Measured on a copy of the author's database (sample, 2026-10-06): grouping a 4,032-request session (782k input blocks, 8,532 distinct hashes) takes about 1.2 s, a 570-request session about 0.5 s. Without a cache every request to the endpoint (first page, "Show more", sort toggle, in-context filter) repeats that pass.

## What 4b does instead (and what this plan relies on)
4b runs the grouping **once per request** into a TEMP table of per-hash/file/source aggregates and derives rows, `total_rows`, the summary, sorting, `in_context` filtering and pagination from it. The aggregation is a single function `aggregate(scope, group, filters) -> table` that does **not** depend on `sort`, `limit`, `offset` or `in_context`. That is the seam the cache wraps; no 4b design choice blocks it or has to change.

## Design (when implemented)
- In-process cache like `db/block_occurrence_service._membership`: key = (database, session id, request count, scope, conversation key, group, category/block_type/source filters); value = the aggregate rows (≤ ~10⁴ small tuples); TTL 60 s; at most 8 entries; one entry per key family; cleared by a `clear_hotspots_cache()` used by tests.
- Invalidated immediately when the session's request count changes (same rule as the membership cache); in-place lineage edits may lag by the TTL.
- Cache hits serve paging/sort/in-context from memory (target ≤ ~50 ms); the first call keeps the 4b budget.

## Tests
Hit on paging/sort/in-context change; miss on a new request, a different filter, scope or conversation; eviction at the entry limit; results identical with and without the cache.

## Open
Whether the first-call time (~1.5 s on a 4,000-request session) is acceptable once measured in the UI; if it is not, a persisted aggregate is a separate, bigger idea.
