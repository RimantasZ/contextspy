# Plan 3b: Session lifecycle and explicit archive

Status: **reviewed and decided 2026-10-05; not started.** Depends on [db-compact.md](db-compact.md) (Plan 3a) for the file to shrink. Part of
[ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md). Decisions D3, D4, D14, D17.

## Why

The current retention policy was a prototype decision and now causes more problems than it solves: `db/database.py:startup_vacuum` silently nulls raw bodies
(`raw_*`, `canonical_*`, `response_events`) and deletes `block_contents` older than `[retention]` days (default 7, `config.py: RetentionSettings`) **only at server
startup**, by request timestamp, regardless of session state. A user analysing a week-old session finds content gone with no explanation. Retention must be explicit
and visible, and deleting content must actually give disk back (Plan 3a).

## Decisions (user)
- Lifecycle `active → ended → archived`. **Archive is an explicit action on the session screen, one-way, with confirmation. No auto-archive in the first version.**
- Analysis views must keep working on archived sessions (block rows stay: hash, type, category, tokens, source key, JSON path). Content-less blocks stay visible with their token counts.
- **Old time-based purge: default off (`0`) with a startup notice** when a config explicitly enables it (user, 2026-10-05).
- Shrink the file with incremental auto-vacuum (Plan 3a): archive reclaims space online when the database is in that mode.

## Review findings that shape this plan (2026-10-05)
1. **`maintenance_lock.py` is not involved.** It is a server-lifetime flock that stops *other processes* from swapping the database file. Archive runs inside the server, so the draft's "respect the maintenance lock" is dropped. The relevant concurrency is SQLite's: capture writes retry on busy (`proxy/capture_writer.py`), so archive keeps its write transactions short.
2. **Deleting rows does not shrink the file** (measured: 6.60 GB file, 65% free pages). Hence Plan 3a and the online `incremental_vacuum` step below.
3. **Archive is cheap**, so no batching is needed. On a scratch copy of the author's database, archiving the session with the most body bytes (564 requests, 616 MB of bodies): nulling bodies **0.3 s**, content cleanup **4.4 s** (1,093 of 1,490 content rows were unreferenced elsewhere). Freed pages: ~427 MB reclaimed by `incremental_vacuum` in 8.7 s.
4. **The content cleanup rule in the draft was incomplete.** A content row may be deleted only if no block *outside this session* references its hash, and requests with `session_id IS NULL` count as outside (the sample has 376 such requests). Content shared with another session stays until that session is archived too.
5. **The CLI session commands call the HTTP API with a 5 s timeout** (`cli.py`). `session archive` needs a longer timeout (use 300 s).
6. **The default change only affects configs without a `[retention]` section.** The author's `config.toml` has none, so the 7-day defaults apply today and a new default of 0 stops the purge for them; configs generated from the old template contain an explicit `raw_body_days = 7` and keep purging, hence the startup notice.
7. **Request payloads cannot tell *why* content is missing.** `Request.to_dict` has no retention state; `content_purged` is per block. Add `content_state`.
8. Capturing into an archived session is not expected (an archived session is not active), but a request that started before the session ended can complete later. Archive is therefore **idempotent**: archiving an archived session runs the purge again and reports what it removed.
9. Archiving removes the canonical request/response, so later re-analysis from bodies is impossible for those requests (migrations such as the v9 `json_path` backfill can only use retained documents). Recommend `contextspy db-upgrade` before archiving (the server already refuses to start while a data migration is pending).

## Design

### Status
`Session.to_dict` gains `status`: `archived` if `archived_at` is set, else `active` if `is_active`, else `ended`. (`archived_at` already exists, added by WI-0 in schema v9; `is_active`/`ended_at` are unchanged.) No schema change.

### `POST /api/sessions/{session_id}/archive`
- 404 unknown session; **409 if the session is active** ("end the session first"). Ended and already-archived sessions are allowed.
- One request, short transactions, implemented in `crud.archive_session(db, session_id)` (and a service function for the file-space step):
  1. **Measure** what will go: count requests with any body column non-null and `SUM(length(...))` of those columns; count the candidate content rows and their `SUM(length(content))`.
  2. **Null the bodies** of the session's requests: `raw_request_body`, `raw_response_body`, `canonical_request_body`, `canonical_response_body`, `response_events`.
  3. **Delete unreferenced content**: candidates = distinct non-null `content_hash` of the session's blocks; delete those candidates not referenced by any block of a request outside the session (`session_id IS NULL OR session_id != :sid`).
  4. Set `archived_at = now (UTC)` if not already set. Commit.
  5. **Reclaim file space** (outside that transaction): if `PRAGMA auto_vacuum` is INCREMENTAL, run `PRAGMA incremental_vacuum(N)` in chunks, consuming each statement fully and committing per chunk, until the free list is empty or a time budget (30 s) is spent. Otherwise skip. Note: with Python's `sqlite3`, `incremental_vacuum` only does work as its result rows are fetched, so fetch them.
- Response: `{"session": {...status...}, "freed": {"requests": n, "request_body_bytes": n, "content_rows": n, "content_bytes": n}, "space": {"auto_vacuum": "incremental"|"none", "reclaimed_bytes": n, "free_bytes_remaining": n, "note": "run `contextspy db-compact`..." | null}, "already_archived": bool}`.
- Broadcast `{"event": "session_archived", "data": session}` like `session_ended`; the UI invalidates its session/request queries.
- A process-level lock per session id prevents two simultaneous archives.

### Retention settings
`config.py: RetentionSettings` defaults become `0`/`0`; the generated config template follows (it renders the settings). `startup_vacuum` logs one INFO line at startup when either value is > 0: "Time-based purge is enabled (N days); sessions can instead be archived explicitly (`contextspy session archive`)". Rename nothing. Update the comments in the template, `docs/development.md` (~l.119), `docs/faq.md` (~l.202), `README.md` (~l.119), and the changelog (behaviour change: bodies are no longer deleted after 7 days unless configured).

### Request detail: why is content missing?
- `Request.to_dict(include_raw=True)` adds `content_state`: `retained` (any canonical/raw body present), else `archived` (its session has `archived_at`), else `not_retained`. Not computed for list responses (it would add a session lookup per row).
- UI: a notice in Request detail (next to `CaptureNotice`): archived → "This session was archived on <date>: raw payloads and block text were removed. Token counts, block structure and analysis remain."; `not_retained` → "Raw payloads for this request are no longer stored." (neutral; this includes everything purged by the old time-based policy). Existing per-block "purged" wording stays.

### UI
- **Archive** button beside **End session** / **Delete** in `pages/SessionDetail.tsx` and on each row of `pages/Sessions.tsx`; disabled with a tooltip for the active session; hidden once archived. An **Archived** badge in the header and the list. **End session** is hidden for archived sessions.
- `components/ArchiveSessionModal.tsx` (pattern: `DeleteSessionModal`, focus on Cancel, Escape closes): states what is removed (raw request/response payloads and block text) and what stays (token counts, block structure and categories, source and JSON paths, lineage and conversation analysis), that it cannot be undone, and the primary button reads "Archive session". After success it shows the `freed` and `space` figures in place before closing.
- Hooks in `api/hooks.ts` (`useArchiveSession`, invalidating `sessions`, the session, and request queries) and types in `api/client.ts` (`Session.status`, `archived_at`; `Request.content_state`).

### CLI
`contextspy session archive <id-or-prefix> [--yes]`: resolve against `GET /sessions` (unique prefix), refuse for active sessions, confirm unless `--yes`, call the API with a 300 s timeout, print the freed/space figures. `session list` gains a **Status** column.

## Compatibility with other plans
- Plan 1 (occurrences), Plan 4 (hot spots), the lineage graph and every classification field use block rows only, so they keep working. Contents of archived blocks are `null` with `content_purged: true` (D3).
- Plan 5 (context tree) must treat archived requests' raw JSON as unavailable; `content_state` is the signal.

## Tests
Backend (`tests/test_session_archive.py`):
1. 404; **409 for an active session**; archiving an ended session nulls all five body columns and sets `archived_at`; `status` reads `archived`.
2. Block rows survive untouched (ids, hashes, tokens, source keys, JSON paths); `get_blocks` returns `content: null`, `content_purged: true`.
3. Content shared with a non-archived session is kept; content shared only with a `session_id IS NULL` request is kept; content used only by this session is deleted; content shared with another *archived* session is deleted once the other references are gone.
4. Idempotent: archiving again succeeds, `already_archived: true`, `archived_at` unchanged, and a body added after the first archive is purged.
5. Freed counts equal the measured bytes/rows; `space` reports `none` for a legacy database and reclaims pages (file shrinks) for an incremental one (use `init_db` on a new path).
6. Occurrences endpoint, lineage graph and `crud.get_stats` return the same numbers before and after archiving.
7. Concurrency: a capture write during an archive succeeds (SQLite busy retry) and a second archive of the same session in parallel is rejected or serialised.
8. `content_state`: retained / archived / not_retained; absent from the list response.
9. Retention defaults are 0; the startup notice is logged only when a value is > 0 (`caplog`); a config with an explicit `[retention]` is still honoured.
10. `session_archived` is broadcast.
CLI: `session archive` prefix resolution, active-session refusal, `--yes`, status column. Frontend (Vitest): Archive button states (active/ended/archived), modal copy and confirm flow, result summary, badge, `content_state` notices, hooks invalidate the right queries. Run `npm run check`.

## Docs
`SPEC.md` (schema/API: `status`, `content_state`, the endpoint, retention defaults), `docs/development.md`, `docs/faq.md`, `docs/cli.md`, `docs/changelog.md`, README retention paragraph.

## Not in scope
Auto-archive by age, un-archive, per-conversation archive, a "keep content" flag (only meaningful with auto-archive; dropped), archive from the dashboard.

## Open questions
None blocking. To revisit later: auto-archive after N days (the former option (c) of the retention question).
