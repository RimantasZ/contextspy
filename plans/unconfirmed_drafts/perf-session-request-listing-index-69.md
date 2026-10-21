# Perf: listing a session's requests is slow on unarchived sessions

Status: **DRAFT / proposal, not decided.** GitHub issue [#69](https://github.com/RimantasZ/contextspy/issues/69) (`perf:`). Found while measuring Plan 4b ([hot-spots.md](../archive/hot-spots.md)); part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). Small and independent of [#68](perf-cold-lineage-analysis-68.md).

## Problem
`SELECT id, session_seq, timestamp, context_fidelity FROM requests WHERE session_id = ?` (used by `block_occurrence_service.scope_for_session`, so by "Present in" and Hot spots) takes **0.6-0.9 s for a 570-request unarchived session** on a copy of the author's database (sample, 2026-10-06). The planner uses `idx_requests_session_seq_unique (session_id=?)` and then visits every row; `requests` keeps large body columns (`raw_request_body`, `canonical_request_body`, ...) inline, so reading a column that comes after them (`context_fidelity`) makes SQLite walk the row's overflow pages. Archived or purged sessions are fast because the bodies are gone.

## Measured fix (on the scratch copy)
A covering index `(session_id, id, session_seq, timestamp, context_fidelity)`: plan becomes `SEARCH requests USING COVERING INDEX`, **0.000 s** for the same 570 rows (was 0.58 / 0.94 s). Building it on 7,228 requests took 0.4 s; its size is a few hundred KB (about 100 bytes per request).

## Proposal
1. Declare the index in `db/models.py` (`Index("idx_requests_session_cover", Request.session_id, Request.id, Request.session_seq, Request.timestamp, Request.context_fidelity)`) and add `CREATE INDEX IF NOT EXISTS ...` to `db/database.py: _migrate()` (the pattern used for `idx_blocks_source_key`). Additive: no data migration, no `SCHEMA_VERSION` bump, applied automatically at startup (per `AGENTS.md`, "Database schema changes").
2. Keep the query column list as the index's column list; add a comment next to the query that changing it silently loses the covering property, and a test that asserts `EXPLAIN QUERY PLAN` mentions `COVERING INDEX` (same idea as the hot-spots plan test).
3. Re-measure the other per-session reads on a scratch copy and record numbers here:
   - `session_lineage_service.evidence_revision` selects ~18 request columns (a covering index that wide is not worth it) and block rows: see [#68](perf-cold-lineage-analysis-68.md).
   - `crud.get_sessions_summary`, request lists with `?session_id=` and `limit=500` (`useRequests`), `crud.get_stats`.
4. Longer term (separate decision): move the large body columns out of `requests` into a side table so every narrow read of `requests` is fast regardless of indexes. Large migration (6+ GB databases, `VACUUM`/space needs, see `db-compact.md`); only worth it if step 3 finds more offenders.

## Risks
- Every insert/update of `requests` maintains one more index (negligible at capture rates; measure on a bulk insert test if worried).
- The index is built at first startup after upgrade; 0.4 s on 7k requests, expect seconds on very large databases; startup time should be checked on the biggest sample.
- Must not change results: the new test suite should stay green unchanged.

## Acceptance (proposal)
Same query on a 570-request unarchived session under ~50 ms on the sample; plan test asserts the covering index; no schema version change; existing tests pass.
