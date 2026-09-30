# Prevent SQLite read contention from dropping captured requests

## Status

Postponed implementation plan. No runtime or database changes have been made for this plan.
The observed `sqlite3.OperationalError: database is locked` occurred while committing a
WebSocket capture. The exact connection holding the lock at that instant is not known; the
read/write contention described below is a strong, testable explanation, not a proven
identification of that particular reader.

## Problem and current behavior

- `db/database.py:init_db()` does not set a journal mode. The inspected local database was
  using SQLite's default `DELETE` (rollback-journal) mode. Long-lived readers can delay a
  writer's commit in this mode.
- Dashboard, Session Detail, and lineage routes use `db.begin_nested()` to pin a consistent
  snapshot while hashing evidence and, on a cache miss, building the graph. This consistency
  guarantee must be preserved. A synthetic 2,000-request repeated-context cold dashboard
  read previously took about 4.4 seconds; actual read durations vary with the database.
- `_save_request()` writes the request, blocks, tool stats, and session sequence within
  `get_db()`. The observed failure was at `db.commit()`. `get_db()` rolls the transaction back;
  the WebSocket handler logs and discards the completed exchange. The provider response can
  succeed while ContextSpy loses that capture. HTTP, SSE, and WebSocket paths all call
  `_save_request()` and need the same protection.
- A nearby `409` from `/sequence/{request_id}/context` means its revision became stale. It is
  expected during concurrent capture and is not itself the database-lock error.

## Outcomes and constraints

1. Preserve the pinned, internally consistent read snapshots and current lineage decisions.
2. Let normal reads overlap capture commits; transient lock contention must not silently
   discard a captured request.
3. Keep one atomic capture transaction: request, `session_seq`, blocks, and tool stats either
   all commit or all roll back. Broadcast `new_request` only after a confirmed commit, once.
4. Keep retry latency bounded so a proxy callback cannot stall indefinitely. Do not log raw
   bodies, provider payloads, or block content when diagnosing failures.
5. Support both fresh and existing local database files without a schema/data migration or
   `db-upgrade` requirement. WAL is a persistent SQLite file setting, not a model change.

## Implementation sequence

### 1. Establish a deterministic concurrency baseline

- Add a temporary-file, two-connection test that holds a pinned read snapshot while a writer
  commits. In `DELETE` mode with a deliberately short busy timeout, reproduce the commit-time
  `SQLITE_BUSY`; in WAL mode, assert the writer commits and the reader retains its original
  snapshot. Use synchronization barriers rather than sleeps or the user's live database.
- Measure pinned-read duration and write/commit wait time for normal, 2,000-request, and
  repeated-context synthetic sessions. Record the journal mode and number of concurrent
  connections. This validates the suspected mechanism and gives a retry-budget baseline.

### 2. Make existing backup/maintenance behavior WAL-safe

- `db/migrations.py:create_migration_backup()` currently uses `shutil.copy2(db_path, ...)`.
  Once WAL is active, a main-file-only copy can omit committed pages or be inconsistent.
  Replace it with SQLite's online backup API while keeping the current versioned `.back`
  naming, CLI output, and restore expectations. Verify the backup includes data committed to
  an active WAL and passes `PRAGMA integrity_check` when opened independently.
- Audit `db-upgrade`, `reset-db`, `db-stats`, `report`, and documented manual backup/restore
  steps. No routine operation should treat the main `.db` file as the complete live database.
  Document that an offline file copy requires a fully stopped/checkpointed database, while
  an online backup should use the backup API. Avoid an automatic full extra copy on every
  startup, especially for large databases.

### 3. Enable WAL safely in the shared database initialization path

- In `db/database.py:init_db()`, configure file-backed SQLite to `PRAGMA journal_mode=WAL`
  **before** `create_all()` and `_migrate()`. Check the returned mode; never assume the pragma
  succeeded. Skip WAL for in-memory test databases, where WAL is not applicable.
- Set an explicit per-connection busy timeout (through SQLite connection configuration or a
  connection event). Start with a measured, bounded value rather than relying on Python's
  implicit default. Do not change `synchronous` from its existing/default safety level merely
  for speed. Ensure newly pooled connections receive the timeout.
- If an existing database cannot enter WAL because another process holds it or its filesystem
  cannot support WAL, fail startup with an actionable error rather than silently continuing
  in `DELETE` mode while claiming the fix is active. Preserve the database unchanged on failure.
  Test idempotent restart of an already-WAL database and all CLI paths that call `init_db()`.
- WAL creates `-wal` and `-shm` sidecars. Never delete or manually copy/rename them while the
  database is live. Keep SQLite's default automatic checkpoint initially; observe WAL size
  under long reads before introducing custom checkpoint policy. SQLite documents that WAL
  allows readers and writers to overlap but does not eliminate every `SQLITE_BUSY` case.

### 4. Retry only transient capture transactions

- Extract the persistence portion of `ContextSpyAddon._save_request()` into a small capture
  writer/repository callable used by HTTP, SSE, and WebSocket captures. Prepare an immutable
  capture envelope once, including stable request UUID, timestamp, capture-time session ID,
  canonical provider IDs, and analyzed data. Do not recompute or reassign those on retry.
- Retry the **entire** `with get_db()` transaction in a fresh SQLAlchemy session after rollback,
  not just the failed `commit()` or final `INSERT`. Each attempt must allocate `session_seq`
  inside its own transaction so rollback leaves no partial sequence advancement.
- Retry only identifiable transient SQLite busy/locked errors (including a busy snapshot, if
  surfaced) by inspecting the wrapped DB-API error/code. Do not retry integrity violations,
  disk-full/corruption errors, arbitrary SQLAlchemy `OperationalError`s, or normalization
  failures. Use bounded backoff with jitter and a measured total wall-clock budget. Start with
  a provisional target of at most ~2 seconds of additional capture-callback time; tune from
  the baseline rather than stacking multiple five-second waits. If that budget proves
  insufficient, design a bounded persistence worker instead of sleeping longer on the proxy
  callback; define queue capacity, shutdown drain, and overflow behavior explicitly.
- Guard ambiguous commit outcomes: before re-inserting, check for the same stable request ID
  in a fresh session. Existing provider-response-ID deduplication remains a secondary check,
  not the sole idempotency key (some captures lack that ID). Emit the WebSocket notification
  only once after persistence is confirmed. On exhaustion, log a concise high-severity
  `capture_not_saved` event with request ID/transport/attempt count and no content; do not
  imply the provider request failed.

### 5. Test and release gates

- Unit/integration tests force `SQLITE_BUSY` before the first write and at commit. Assert
  rollback, fresh-session retry, one request row, one `session_seq`, complete blocks/tool stats,
  and one broadcast. Repeat for captures with and without provider response IDs, plus HTTP
  and WebSocket entry points. Non-lock errors must fail without retry.
- Concurrent-read test: hold a real dashboard/lineage snapshot, persist a new request, assert
  prompt success in WAL mode, and verify the reader still returns one consistent revision.
  Preserve the existing stale-revision `409` behavior.
- Run `pytest`, existing migration tests, and a 2,000-request benchmark. Add a short local
  stress run with repeated context and ongoing capture; acceptance is zero lost/duplicated
  captures, bounded callback latency, and no long-lived/unbounded WAL growth. Do not turn a
  single synthetic timing into a p95 release claim.
- Roll out on a stopped ContextSpy instance. Confirm a recoverable backup and free disk space;
  on first start log the selected journal mode without exposing the DB contents. Verify an
  existing database converts once and restarts cleanly. Provide rollback instructions that
  stop all connections, checkpoint through SQLite, then deliberately switch back to `DELETE`
  if needed—never remove `-wal`/`-shm` files by hand.

## Out of scope

- Changing conversation/lineage inference, API response semantics, or UI rendering.
- Persisting derived lineage or introducing a schema-version migration solely for WAL.
- A durable on-disk capture spool or exactly-once guarantee across process/power failure.
  If bounded retries still lose captures in production, design that separately with explicit
  privacy, disk-limit, and recovery semantics.

## References

- [SQLite WAL: concurrency, persistence, checkpoints, and remaining busy cases](https://www.sqlite.org/wal.html)
- [SQLite locking in rollback-journal mode](https://www.sqlite.org/lockingv3.html)
- [SQLite online backup API](https://www.sqlite.org/backup.html)
- [SQLite cautions about copying a live DB without its journal](https://www.sqlite.org/howtocorrupt.html)
