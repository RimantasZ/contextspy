# Perf: cold conversation (lineage) analysis is slow on long sessions

Status: **DRAFT / proposal, not decided.** GitHub issue [#68](https://github.com/RimantasZ/contextspy/issues/68) (`perf:`). Found while measuring Plan 4b ([hot-spots.md](../archive/hot-spots.md)); part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). Not blocking any plan, but every conversation-scoped feature inherits it.

## Problem
Anything that needs the conversation structure of a session builds the **lineage graph** first: the Conversations view, the block "Present in" panel in conversation scope, Hot spots (conversation selector and default scope), and any future tree/compare page. Measured on a copy of the author's database (samples, one machine, 2026-10-06):

| Session | Cold (graph not built in the process) | Warm graph, membership rebuilt | Membership cached |
|---|---|---|---|
| 4,032 requests | ~65 s | ~5 s | 0.08 s |
| 570 requests | ~7 s | ~1.5 s | 0.04 s |

The 60 s membership cache of `db/block_occurrence_service.py` hides it after the first call (and, since 2026-10-06, counts its lifetime from the end of the build), but the first call per session per minute pays the full price, and even a "warm" graph costs seconds because the revision check runs on every call.

## Where the time goes
cProfile of a cold build of the 570-request session (27 s under the profiler, ~7 s without; the *shares* matter):
- **~67% `analysis/lineage.py: build_lineage_graph`**: `_score_candidate` calls `analysis/context_diff.py: diff_contexts` ~6,500 times (14 s of 27 s), of which the LCS matching (`_lcs_mappings`) is ~5 s.
- **~19% `db/session_lineage_service.py: evidence_revision`**: hashes the evidence columns of every request **and every block row** of the session on each call, cache hit or not (5 s profiled; 1.5 s warm on 570 requests, 3.7-6.7 s on 4,032).
- **~14% loading** request snapshots and block rows (`crud.get_session_lineage_snapshots`, `_lineage_snapshots_for_requests`).

## Constraints
- Lineage and conversation membership are **derived at read time and never persisted** (`session_lineage_service.py` header; roadmap "Repository policies"). Caching in process memory is fine; persisted columns encoding membership are not.
- Conversation membership must stay identical to the dashboard's definition (D5); any speed-up must keep `tests/test_lineage*.py` results unchanged.
- Capture must not slow down.

## Ideas to evaluate (none decided)
1. **Cheaper revision check.** Replace "hash every block row" with a bounded fingerprint: request count, max request id/rowid, and a per-request change marker (e.g. `tokens_total_input`, `session_seq`, block count per request via one `GROUP BY`), keeping the evidence columns already hashed for requests. Must still catch in-place edits and deletes the current check is documented to catch (see the header comment); add tests for each.
2. **Cheaper candidate scoring.** Skip `diff_contexts` when exact predecessor evidence (`provider_response_id` / `predecessor_response_id`) already decides the parent; bound the candidate set per request (e.g. most recent N by token similarity); memoise `diff_contexts` per pair of block-hash tuples; compare hash tuples directly for the common "child extends parent" case before running an LCS.
3. **Warm ahead of use.** Build the graph in the background after capture (debounced) or when a session page is opened, so the first interactive call is a cache hit. Needs care for memory (`CACHE_ENTRY_LIMIT`, `CACHE_BYTES_LIMIT`) and for never blocking the proxy thread.
4. **Incremental update** when a request is appended to an existing session (parent search only for the new request). Largest change; only if 1-3 are not enough.
5. **Do less in the callers that only need membership:** `block_occurrence_service._membership` needs `groups`, `filed_under`, `codes`; check whether a reduced graph build suffices.

## How to measure (before and after)
On a scratch copy made with SQLite's online backup API (never the live DB): clear `session_lineage_service._cache` and `block_occurrence_service.clear_membership_cache()`, then time `scope_for_session(db, sid, "conversation", with_conversations=True)` cold, warm-graph and cached, for a ~570-request and a ~4,000-request session; profile with cProfile and flush stdout before `os._exit`. Record the numbers in this file.

## Acceptance (proposal)
Cold membership for a 4,000-request session under ~10 s without changing any lineage test result; warm lookups under ~0.5 s; the existing lineage and occurrence tests pass unchanged; new tests for the cheaper revision check (detects an edited, deleted and reassigned request/block row).

## Open questions
1. Acceptable first-load time for a 4,000-request session (target)? Is background warming acceptable on the user's machine (CPU while capturing)?
2. Is losing exact detection of in-place edits to *unrelated* block columns acceptable for the revision check (e.g. attrs edits), or must the full hash stay on a slow path?
3. Does the Conversations view itself have a separate cost beyond membership (`get_session_conversations`)? Measure before assuming one fix covers both.
