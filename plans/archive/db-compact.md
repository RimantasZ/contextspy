# Plan 3a: `contextspy db-compact` — reclaim free space, enable automatic shrinking

Status: **implemented 2026-10-06, committed in `f34fa29`; not released; the author's live database has NOT been compacted.** See "Implementation status" at the end. First half of the former "Plan 3: session archive"; the second half is
[session-archive.md](session-archive.md) (Plan 3b), which depends on this. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).

## Why (measured, author's database, 2026-10-05; a sample, not a rule)

- The live file is **6.6 GB, of which 1,043,851 of 1,612,023 pages (~4.3 GB, 65%) are free pages**. The existing startup purge
  (`db/database.py:startup_vacuum`) deletes content and nulls bodies, but SQLite keeps freed pages in the file and nothing ever runs
  `VACUUM` (`PRAGMA auto_vacuum` = 0). Despite its name, `startup_vacuum` does not vacuum.
- On a scratch copy: `VACUUM` took **18 s** and shrank the file **6.60 → 2.36 GB** (`requests` ≈ 1.66 GB of it, `blocks` ≈ 0.4 GB, indexes ≈ 0.23 GB).
- After `PRAGMA auto_vacuum=INCREMENTAL` + `VACUUM`, freeing ~427 MB of bodies and running `PRAGMA incremental_vacuum` took **8.7 s** and shrank the file
  2.39 → 1.96 GB, online (no restart, short write transactions).
- Any "archive" or retention feature that deletes rows is pointless for disk usage unless the file can shrink. That makes this the foundation of Plan 3b, and it is
  useful on its own: the user gets ~4 GB back without waiting for anything else.

## Decisions (user, 2026-10-05)
1. Ship compaction **before** archive, as its own deliverable (no UI, no schema change).
2. Use **incremental auto-vacuum**: `db-compact` converts the database; new databases start that way; archive (3b) then shrinks the file online.

## Deliverables

### 1. `contextspy db-compact [--yes] [--backup]`
Offline command (the server must be stopped), implemented in a new `contextspy/db/compaction.py` with the CLI as a thin wrapper in `cli.py`. Use the stdlib `sqlite3` module directly,
`isolation_level=None` (VACUUM cannot run inside a transaction), not SQLAlchemy.

Steps:
1. Resolve `Settings.load().storage.db_path`. Missing or empty file → message, exit 0.
2. `acquire_database_lock(db_path)` (`db/maintenance_lock.py`). If it fails the server (or another maintenance command) is running: print its existing message and exit 1. The lock is held for the whole run.
3. **Inspect** (read-only): `page_size`, `page_count`, `freelist_count`, `auto_vacuum`, file size and `-wal` size. `live_bytes = (page_count - freelist_count) * page_size`.
4. **Nothing to do**: if `auto_vacuum == 2` (INCREMENTAL) and `freelist_count * page_size` < 1% of the file (or < 16 MB) → "Already compact", exit 0.
5. **Free-space preflight**: VACUUM writes a full copy of the live data before replacing the file. Require `shutil.disk_usage(db_dir).free >= live_bytes * 1.25 + 64 MB`; otherwise abort *before touching anything*, printing the numbers needed and available.
6. Print what will happen (current size, expected size ≈ `live_bytes`, "stop ContextSpy first", estimated duration order of magnitude) and ask for confirmation unless `--yes`.
7. `--backup`: take a snapshot first with `db/backups.create_backup(db_path, current_schema_version, purpose="pre_compact")`. This **requires** extending `BackupPurpose` and `backup_name` (name `{stem}_backup_v{N}_pre_compact_{stamp}.back`) **and** the second pattern in `list_backups` (`(?:_pre_restore|_pre_compact)?`), otherwise the snapshot is not listed by `db-restore` and cannot be found again. `create_backup` already verifies the snapshot's schema version equals `N` for non-migration purposes. Without `--backup` no copy is made: `VACUUM` is atomic (the original file is untouched until it commits), so the preflight is the safety net.
8. Run: `PRAGMA wal_checkpoint(TRUNCATE)`, `PRAGMA auto_vacuum = INCREMENTAL`, `VACUUM`, `PRAGMA wal_checkpoint(TRUNCATE)`. Verify `PRAGMA auto_vacuum` == 2 afterwards and run `PRAGMA quick_check` (fail loudly if it is not `ok`).
9. Report before/after file size, reclaimed bytes and elapsed time. Release the lock in a `finally`.

Interruption: Ctrl-C or a crash mid-VACUUM leaves the original database intact (SQLite rolls back); leftover temp files are SQLite's and are removed by it. State this in the help text.

### 2. New databases are created with incremental auto-vacuum
In `db/database.py:init_db`, when the file is new/empty (`PRAGMA page_count == 0` before `create_all`), execute `PRAGMA auto_vacuum = INCREMENTAL` **before** any table is created. Existing databases are left alone (only `db-compact` converts them). Add a test that a fresh database reports `auto_vacuum == 2` and that an existing one is unchanged. **Verify** that setting it after `journal_mode=WAL` but before `create_all` works on a fresh file; if the order matters, set it first.

### 3. Visibility
- `contextspy db-stats` prints file size, free (reclaimable) space and auto-vacuum mode under the table counts, and suggests `db-compact` when reclaimable space is ≥ 20% of the file.
- `docs/cli.md`, `docs/faq.md` (where `[retention]` is discussed), `docs/changelog.md`, `docs/development.md` ("Data storage"): document the command, why the file never shrank, and that `startup_vacuum` frees pages without shrinking the file.

## Backups and restore (existing backups are unaffected)
- Backups (`db/backups.py`) are complete standalone rollback-journal copies made with SQLite's backup API; `VACUUM` rewrites only the live file, so every existing `.back` file (including pre-v9 ones) stays valid. Restore validates a backup by file format, `quick_check`, required tables and schema version <= this build; page layout and `auto_vacuum` play no part.
- A restored backup comes back **as it was**: not compacted, `auto_vacuum` 0, its old schema version (then `db-upgrade`). Compaction must be re-run to shrink it and re-enable incremental mode; archive (Plan 3b) detects the non-incremental mode and says so instead of shrinking.
- A backup copies every page including free ones: compacting first makes later backups smaller (the author's 6.6 GB file would back up as ~2.4 GB).

## Tests (`tests/test_db_compact.py`)
1. Build a DB with `init_db`, add large rows, delete them: `freelist_count` grows and the file does not shrink; `compact()` shrinks it, row data is unchanged, `auto_vacuum == 2`, `quick_check == ok`.
2. A database created with `auto_vacuum = 0` (legacy shape, built directly with sqlite3) is converted to 2 by compaction.
3. Refuses while the maintenance lock is held (acquire it in the test); the file is byte-identical afterwards.
4. Free-space preflight: monkeypatch `shutil.disk_usage` to report too little space → aborts before opening for write; file byte-identical.
5. "Already compact" path exits without running VACUUM (assert via a spy).
6. `--backup` creates a restorable snapshot with the new purpose; `--yes` skips the prompt; declining the prompt changes nothing.
7. Missing/empty database; WAL file left behind is checkpointed away.
8. `init_db` on a new path → `auto_vacuum == 2`; on an existing legacy file → unchanged.
9. `db-stats` output includes size/free/auto-vacuum lines (CliRunner with a temp DB).
10. **Backups survive compaction**: create a pre-compaction backup (older schema shape included), compact, then `list_backups` still lists it and `restore_backup` of it succeeds; the restored database has `auto_vacuum == 0` and its original rows. A `--backup` snapshot is listed by `list_backups` and restorable (round trip).

## Not in scope
Auto-compaction at startup (needs exclusive access and can take minutes on large files), a UI button, background incremental vacuum timers. Online shrinking after deletions belongs to the archive action (Plan 3b).

## Risks
- Disk-full during VACUUM is prevented by the preflight but not impossible (other processes); the original stays intact.
- Running it while another tool holds the DB open (not ContextSpy) is not detected by the flock; the SQLite busy timeout will fail the VACUUM instead.

## Implementation status (2026-10-06)

**Implemented** (backend 519 tests passing at the time; committed in `f34fa29`):
- `contextspy/db/compaction.py`: `inspect_space`, `needs_compaction`, `required_free_bytes`, `check_free_space`, `compact_database(db_path, backup=, confirm=, report=, temp_dir=)` returning a `CompactionOutcome` (`compacted` / `already_compact` / `declined` / `empty`), `CompactionError`.
- `contextspy db-compact [--yes] [--backup]` (`cli.py`), also listed in `contextspy help`; refuses while a backend answers on the configured web port (`_configured_backend_reachable`, which also catches older builds) in addition to the maintenance lock.
- `db/backups.py`: `pre_compact` purpose, name `{stem}_backup_v{N}_pre_compact_{stamp}.back`, recognised by `list_backups` (and therefore `db-restore`/`status`).
- `init_db`: new/empty database files get `PRAGMA auto_vacuum=INCREMENTAL` (existing files untouched).
- `contextspy db-stats`: file size, free space, auto-vacuum mode and a `db-compact` hint.
- Docs: `docs/cli.md`, `docs/faq.md`, `docs/development.md`, `docs/changelog.md`; `startup_vacuum` docstring says it does not shrink the file.
- Tests: `tests/test_db_compact.py` (26): space maths, shrink + rows kept + mode 2, nothing-to-do, missing/empty, lock held, disk-space preflight (database volume and a different temp volume), failed VACUUM leaves the file byte-identical and the lock released, busy database, declined confirmation, new-database mode (and existing untouched), online `incremental_vacuum`, backup naming/discovery, **existing backup survives compaction and restores (restored file is mode 0 and can be compacted again)**, `--backup` snapshot listed and restorable, backup space requirement, CLI paths, `db-stats`.

**Verified on real data** (a scratch copy made with SQLite's online backup API from the live database, 7,058 requests / 1.44M blocks; the live file was not modified):
`compact_database(backup=True)`: 6.60 GB (4.18 GB / 63% free, mode 0) → **2.36 GB**, free 0, mode 2; backup 22 s, VACUUM 12 s; the backup (6.60 GB) is listed by `list_backups`; row counts unchanged.

**Differences from the plan text**
- **Pragma order matters (found by experiment):** `auto_vacuum` must be set *before* `journal_mode=WAL` (which writes the header), not just before `create_all`; otherwise it silently stays 0. `init_db` sets both inside one connection block, auto-vacuum first.
- **Free-space rule is more precise than "live × 1.25 + 64 MB":** VACUUM rebuilds in SQLite's temp directory and copies back through the WAL, so the database volume needs `live × 1.25 + 64 MB` (+ the file size and 5% if `--backup`), plus `live × 1.1` more when the temp directory is on the same volume; when it is on a different volume that volume needs `live × 1.1 + 64 MB`. Failures name `SQLITE_TMPDIR`.
- "Nothing to do" is: already incremental **and** free space < max(16 MB, 1% of the file). A non-incremental file is always compacted (conversion is a reason by itself).
- `compact_database` returns an outcome instead of printing, so the CLI and tests share one path; the lock is taken inside it (RuntimeError "in use" propagates, as in `db-restore`).
- The post-compaction check is `PRAGMA quick_check`; a failure raises `CompactionError` *after* the rebuild committed (the message says the integrity check failed), whereas every earlier failure leaves the file untouched.

**Not done / still open**
- The author's live database is not compacted; run `contextspy db-compact` with ContextSpy stopped (use `--backup` if you want a copy; it needs ~6.6 GB free for it).
- Not exercised: Windows (`msvcrt` lock path), a database on a volume with a separate `SQLITE_TMPDIR`, very large databases (> 10 GB).
- Plan 3b (archive) builds on this and is implemented (`a2a4b8c`); the retention default / startup notice change belongs to 3b.

