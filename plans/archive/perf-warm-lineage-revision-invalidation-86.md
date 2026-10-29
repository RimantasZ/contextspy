# Perf: warm lineage calls and polls pay the full revision check

Status: **IMPLEMENTED 2026-10-29 (uncommitted), see "Implementation notes".** GitHub issue [#86](https://github.com/RimantasZ/contextspy/issues/86) (`perf:`). Follow-up of [#68](https://github.com/RimantasZ/contextspy/issues/68) ([archive/perf-cold-lineage-analysis-68.md](perf-cold-lineage-analysis-68.md)), which made the cold build 3.4x faster but left the warm path unchanged. Part of [ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).

## Problem
`session_lineage_service.graph_for_session` computes `evidence_revision` (a fingerprint of every request and block row the graph reads) **before** it looks at the graph cache, on every call. Measured 2026-10-29 on a copy of the author's database (samples, one machine):

| Warm call (graph cached) | 564 requests | 4,032 requests |
|---|---|---|
| conversations (4 groups) | 0.80 s | 2.32 s |
| graph only | 0.80 s | 2.37 s |
| one conversation's request page | 0.81 s | 2.36 s |
| one group by key | 0.79 s | 2.38 s |
| all groups (limit 20) | 0.83 s | 2.32 s |

The projection on top of the graph costs under 0.05 s (Q3 of #68: answered, no separate cost). The whole warm cost is the revision check, and it is paid on **every** call. Worse, the frontend polls without a revision (`ui/src/api/hooks.ts`): `useSessionConversations`, `useSessionSequence` and `useDashboardLive` every 5 s, `useSessionLineage` polls `/lineage/revision` every 5 s. A 4,000-request session therefore costs several seconds of server CPU per 5 s cycle while a view is open. Target: warm calls and polls under ~0.1 s in the steady state, with the revision still exact.

## Facts established (code reading, 2026-10-29)
- The proxy runs as a thread inside the server process (`proxy/runner.py`), so capture writes and graph reads share one process and one in-process cache.
- Writers of the rows the revision reads:
  - capture: `proxy/capture_writer.persist_capture` -> `crud.create_request` and block/tool-stat inserts;
  - `crud.delete_session` (sets `requests.session_id = NULL`) and `crud.delete_session_with_requests`;
  - `db/session_archive.archive_session_data`: nulls the five body columns and deletes `block_contents` text; neither is read by the revision, so the revision should not change (to be tested);
  - data migrations in `db/migrations.py` run before the server starts (`_abort_if_migrations_pending`), so the cache is empty then;
  - out of process: `contextspy` CLI "clear database" (`cli.py`), a second server on the same file, manual SQLite edits.
- No API endpoint edits a request or a block row. The only PATCH is session rename (not read by the graph).
- **External parents:** the revision includes requests from *other* sessions that answer a predecessor of this session's requests (`evidence_revision`, external rows). A capture in session B can therefore change session A's revision. Invalidation cannot be per session only.
- The revision string is public (`/lineage/revision`, the `revision` parameters of sequence/conversation endpoints, the 409 on mismatch). Its meaning and format stay unchanged.

## Design
Make the revision cheap by remembering it and invalidating the memory when a write that could change it commits, instead of recomputing it on every call.

1. **Memo in `session_lineage_service`.** `(engine, session_id) -> (revision, computed_at, generation)`. `graph_for_session`/`evidence_revision` callers use the memo when it is present and not older than the safety TTL; otherwise they compute the exact fingerprint and store it. A steady-state poll then costs a dictionary lookup plus the cached graph.
2. **`invalidate(engine, session_id=None)`.** Drops the memo for one session, or for all sessions when called without one. This is the public hook for any code that changes evidence by other means.
3. **Wire it at the database layer, not at each caller.** An SQLAlchemy engine event watches executed statements; a write (`INSERT`/`UPDATE`/`DELETE`) to `requests` or `blocks` marks the connection dirty, the `commit` event invalidates, and `rollback` clears the mark. This covers capture, both delete paths and any future writer without per-caller wiring (a per-caller call is easy to forget, and capture runs in a different module from the cache). Archive's body-column `UPDATE` also invalidates; that costs one extra recompute and is correct.
   - **Scope:** a write that touches a known session id invalidates that session; a write whose session cannot be determined from the statement, or that carries/depends on `provider_response_id`/`predecessor_response_id` (external parents), invalidates **all** memos. Simplest correct rule to start with: invalidate all sessions on any commit that wrote `requests` or `blocks`. The active session recomputes on its next read anyway (it has just changed); idle sessions recompute once when next viewed (2.3 s on the biggest). Narrow the scope later only if measurements show it matters.
4. **Race safety.** Invalidate **after** commit, and use a generation counter: a reader records the generation before computing the fingerprint and stores the memo only if the generation did not move. Without this, a reader that started before a commit could finish after it and memoise a stale revision.
5. **Safety TTL (~60 s).** Writers outside the process (CLI clear, a second server, manual edits) cannot invalidate the memo. After the TTL the exact fingerprint runs again, so the worst case for such a change is the TTL. First load of every session stays exact.
6. **No client change and no change to revision semantics.** The `revision` parameters and the 409 on mismatch keep working; mismatch is now decided against the memoised revision. The "option 3" idea from the discussion (trust a client-supplied revision and skip the check) is **not** needed and not done, since the memo makes the check cheap for every caller, including the polls that send no revision.
7. **Cache interaction.** Graph cache keys stay `(engine, session, revision)`; when a recompute produces a new revision the old graph entry is replaced as today.

## Tests
- Capture of a request (through `persist_capture`) changes the served revision on the next call, with no TTL wait.
- Delete session with and without requests changes it; disassociating requests of another session that held an external parent changes it.
- External-parent arrival: a capture in session B whose `provider_response_id` is the predecessor of an unanswered request in session A changes A's revision.
- Archive: revision before == after (body columns and `block_contents` are not evidence); the memo may still be dropped, the recomputed value must be equal.
- Rolled-back write does not invalidate (no needless recompute) and does not corrupt the memo.
- Race: a commit between "generation read" and "memo store" leaves no stale memo (deterministic test with a hook between the two steps).
- TTL: with the clock advanced past the TTL, an out-of-band edit (raw sqlite connection, not the engine) is picked up.
- Guard: a test that writes `requests`/`blocks` through each known writer function and asserts invalidation fires, so a new writer that bypasses the engine event (raw `sqlite3` connection) is caught if it is added to the list.
- Existing revision tests in `tests/test_dashboard_stats.py` keep passing unchanged: `evidence_revision` itself stays exact; only `graph_for_session` stops calling it every time.

## Measure
Same method as #68 (scratchpad copy of the database via the backup API, caches cleared, 564- and 4,032-request sessions): warm conversations, request page, group, graph and `lineage/revision` calls; plus a loop of 5 s-style polls with a simulated capture between some of them to show the recompute only follows a write. Record the numbers here.

## Acceptance
Steady-state warm calls and polls under ~0.1 s on the 4,032-request session; first call and the call after a write still exact (about 2.3 s); every test above passes; no change to `evidence_revision` output, to revision strings or to API responses; no extra work on the capture path beyond a flag check per executed statement (verify with a capture benchmark or a statement-count test).

## Risks / open points
- **The engine event sees statements, not meaning.** Parsing table names out of SQL text is brittle (ORM, Core and `text()` statements). Alternative: SQLAlchemy ORM `after_flush`/`after_commit` session events (miss `text()` writes such as the delete paths and archive) or explicit `invalidate()` calls at the known writers plus the guard test. Decide in implementation after checking which statements the engine event can classify reliably; the engine event with a conservative rule (any `INSERT`/`UPDATE`/`DELETE` text mentioning `requests` or `blocks`) is the starting point.
- **Capture cost.** The event fires for every statement on every connection, including the proxy's; it must be a cheap string check and must never raise.
- **Multi-process deployments** are not covered except through the TTL; ContextSpy runs one server process per database today.
- **Memory:** the memo is a few bytes per session.
- **Cold build (17.3 s for 4,032 requests) is unchanged** by this plan; idea 3 (background warming) of #68 stays closed unless first-load time is judged too long.

## Implementation notes (2026-10-29)
Code: `session_lineage_service.revision_for_session` (memo), `invalidate()`, `watch_engine()`; `crud._session_lineage_revision` and `graph_for_session` use the memo; `database.init_db` attaches the events to the engine it creates. Tests: `tests/test_lineage_revision_memo.py` (28 tests); the existing 726 are unchanged and pass (754 in total).

Differences from the design above:
- **`invalidate()` takes no session.** It drops every memo (the external-parent argument made per-session invalidation unsafe anyway). A generation counter replaces the per-session bookkeeping.
- **Three triggers, not one:** the statement that writes `requests`/`blocks` (so the writing transaction reads its own write), the commit, and the rollback of a connection that wrote (a reader may have memoised pending data meanwhile).
- **The memo also stores the request count** and is dropped when it differs, so an append from another process is seen at once, not after the TTL; only in-place edits from other processes wait for the TTL (60 s).
- **The engine event classifies statements with a regex** (`INSERT [OR x] INTO / UPDATE / DELETE FROM / REPLACE INTO` on `requests` or `blocks`, quoted or not); tested on both writers and non-writers (`tool_stats`, `block_contents`, `sessions`, selects). A statement that merely reads `requests` inside a write to another table (archive's `block_contents` cleanup) does not invalidate.
- **Watching:** attached when `init_db` creates the engine, and lazily on first read through any other engine (tests, scripts) as a fallback.
- The memo is bounded to 256 sessions.

Tests cover: one fingerprint for repeated calls; reads and read-only commits/rollbacks do not invalidate; a committed UPDATE/DELETE of requests or blocks, a session reassignment, `delete_session` and `delete_session_with_requests`; a write seen by its own uncommitted transaction; a rolled-back write; a commit after a reader memoised pending data; an external parent arriving from another session; archive leaves the revision equal; a write during the computation is not remembered; out-of-process in-place edit (TTL) and append (count); memo bound; statement classification; a real `persist_capture`. Mutations that each make a test fail: removing the commit/rollback invalidation, the statement-time invalidation, the generation check, the count comparison.

Measured (copy of the author's database, one machine, same method as #68), warm calls on the 4,032-request session: conversations 2.32 s -> 0.006 s, `lineage/revision` 2.3 s -> under 1 ms, request page 2.36 s -> 0.003 s, group by key 2.38 s -> 0.003 s, graph 2.37 s -> under 1 ms; first call after a committed write 2.37 s (one recompute), then 0.006 s. 564-request session: warm 0.80 s -> 0.010 s, after a write 0.86 s. Cold unchanged (17.1 s and 2.8 s). Capture path: one cached regex lookup per executed statement (not benchmarked separately).
