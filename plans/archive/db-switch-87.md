# db-switch: swap databases without losing data (#87)

Status: DONE (2026-10-31), implemented and archived. Design agreed with the user; also posted on the issue
(https://github.com/RimantasZ/contextspy/issues/87).

## Behaviour
- Every DB has a name. The active name is recorded in `<db_dir>/db_switch/.active`.
- `contextspy db-switch <name>` shelves the current DB under its own name (`default` the first time, or
  `--as <name>`), then brings `<name>` into `<db_dir>`.
- Unknown `<name>` is refused unless `--new` is passed (a typo must not silently give an empty DB). With
  `--new` the root is left without a DB and `init_db` creates a fresh one on next start.
- `contextspy db-switch --list` shows shelved names and the active one; `status` shows the active name.
- The shelf is `<db_path.parent>/db_switch` (not a hardcoded `~/.contextspy`) so all moves stay on one
  filesystem (atomic renames).

## What is moved
Only the `.db` file and the `.back` files from `list_backups(db_path)`. Never `-wal`/`-shm`/`-journal`,
never `.lock`, `config.toml`, certs or other files.

## WAL handling
1. Take the maintenance lock (`acquire_database_lock`); held means a server runs, so abort. The CLI also runs
   `_configured_backend_reachable` for older builds that do not take the lock.
2. `backups._checkpoint_and_close` on the outgoing DB: `wal_checkpoint(TRUNCATE)`, `journal_mode=DELETE`,
   verify no sidecars. The shelved DB is standalone.
3. The incoming DB must be standalone too: no sidecars next to it and `inspect_backup` passes (rejects
   schemas newer than this build).
4. `init_db` re-enables WAL on next start. `.lock` stays in place.

## No overwrites
- Whole plan is validated first; any destination that exists (other than files vacated by the same swap)
  aborts.
- Rename without clobber: existence check under the lock, then `os.rename`.
- Swap through `db_switch/.swap-<ts>/`: outgoing -> staging, incoming -> root, staging -> shelf slot.
- Each completed move is journaled in memory and rolled back in reverse on failure. A leftover `.swap-*`
  (crash) makes the next run refuse and name the directory; no automatic recovery.
- Names: `[A-Za-z0-9_][A-Za-z0-9_-]*`, max 64 chars, case-insensitive collision check (macOS).

## Code
- `contextspy/db/switching.py` (logic) + thin `db-switch` command in `cli.py`.
- Tests: `tests/test_db_switch.py` (tmp_path): WAL checkpointed and no sidecars moved, no overwrite,
  rollback on injected failure, refusal when the lock is held, name validation, `--new`, `--list`.
- Docs: `docs/cli.md` (usage), `docs/development.md` ("Database files and WAL" + swap protocol),
  `SPEC.md` command list, `help_cmd` list.
