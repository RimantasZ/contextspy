# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Offline compaction of the SQLite database file (``contextspy db-compact``).

SQLite never returns freed pages to the filesystem on its own: deleting block contents or
nulling request bodies (retention, session archive) only grows the file's free list. ``VACUUM`` rebuilds
the file without it, and switching the database to *incremental* auto-vacuum lets later deletions shrink
the file online (``PRAGMA incremental_vacuum``). See plans/db-compact.md.

Everything here uses the stdlib ``sqlite3`` module directly and runs with the maintenance lock held, so
the server cannot be running. ``VACUUM`` is atomic: an interruption leaves the original file intact.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock

AUTO_VACUUM_NONE, AUTO_VACUUM_FULL, AUTO_VACUUM_INCREMENTAL = 0, 1, 2
_MARGIN_BYTES = 64 * 1024 * 1024
_NOTHING_TO_DO_BYTES = 16 * 1024 * 1024
_NOTHING_TO_DO_FRACTION = 0.01
_BUSY_TIMEOUT_SECONDS = 30


class CompactionError(RuntimeError):
    """A precondition failed or the result could not be verified; the original database is intact."""


@dataclass(frozen=True)
class DatabaseSpace:
    page_size: int
    page_count: int
    freelist_count: int
    auto_vacuum: int
    file_bytes: int
    wal_bytes: int

    @property
    def free_bytes(self) -> int:
        return self.freelist_count * self.page_size

    @property
    def live_bytes(self) -> int:
        return (self.page_count - self.freelist_count) * self.page_size

    @property
    def incremental(self) -> bool:
        return self.auto_vacuum == AUTO_VACUUM_INCREMENTAL

    @property
    def free_fraction(self) -> float:
        return self.free_bytes / (self.page_count * self.page_size) if self.page_count else 0.0


def inspect_space(db_path: Path) -> DatabaseSpace:
    """Page statistics read through a read-only connection (nothing is created or changed)."""
    db_path = Path(db_path)
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        page_size, page_count, freelist, auto_vacuum = (
            conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("page_size", "page_count", "freelist_count", "auto_vacuum")
        )
    wal = Path(f"{db_path}-wal")
    return DatabaseSpace(
        page_size, page_count, freelist, auto_vacuum,
        db_path.stat().st_size, wal.stat().st_size if wal.exists() else 0,
    )


def needs_compaction(space: DatabaseSpace) -> bool:
    """False only when the file is already incremental and has (almost) nothing to give back."""
    if not space.incremental:
        return True  # conversion to incremental auto-vacuum is itself the point
    return space.free_bytes >= max(_NOTHING_TO_DO_BYTES, _NOTHING_TO_DO_FRACTION * space.file_bytes)


def required_free_bytes(space: DatabaseSpace, *, backup: bool = False, temp_on_same_volume: bool = False) -> int:
    """Free space the database volume needs for the run.

    ``VACUUM`` rebuilds the live data in SQLite's temp directory and copies it back through the WAL,
    so the database volume needs roughly the live size again (plus margin); a pre-compaction backup adds a
    full copy of the file; when the temp directory is on the same volume the rebuild needs its own copy too.
    """
    live = space.live_bytes
    needed = int(live * 1.25) + _MARGIN_BYTES
    if backup:
        needed += space.file_bytes + max(_MARGIN_BYTES, space.file_bytes // 20)
    if temp_on_same_volume:
        needed += int(live * 1.1)
    return needed


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def check_free_space(db_path: Path, space: DatabaseSpace, *, backup: bool, temp_dir: Path | None = None) -> None:
    """Raise ``CompactionError`` (before anything is modified) when the disk is too full."""
    db_dir = Path(db_path).resolve().parent
    temp_dir = Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir())
    same = _same_volume(db_dir, temp_dir)
    required = required_free_bytes(space, backup=backup, temp_on_same_volume=same)
    available = shutil.disk_usage(db_dir).free
    if available < required:
        raise CompactionError(
            f"Not enough free disk space to compact safely: need about {required:,} bytes on "
            f"{db_dir}, have {available:,}."
        )
    if not same:
        needed_temp = int(space.live_bytes * 1.1) + _MARGIN_BYTES
        available_temp = shutil.disk_usage(temp_dir).free
        if available_temp < needed_temp:
            raise CompactionError(
                f"Not enough free space in the temporary directory {temp_dir}: need about "
                f"{needed_temp:,} bytes, have {available_temp:,}. Set SQLITE_TMPDIR to a larger volume."
            )


@dataclass(frozen=True)
class CompactionOutcome:
    status: str  # "compacted" | "already_compact" | "declined" | "empty"
    before: DatabaseSpace | None = None
    after: DatabaseSpace | None = None
    seconds: float = 0.0
    backup_path: Path | None = None

    @property
    def reclaimed_bytes(self) -> int:
        if self.before is None or self.after is None:
            return 0
        return max(0, self.before.file_bytes + self.before.wal_bytes - self.after.file_bytes - self.after.wal_bytes)


def _vacuum(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        busy, _log, _checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise CompactionError("The database has active readers or writers; stop every program using it and retry.")
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode != AUTO_VACUUM_INCREMENTAL:
            raise CompactionError(f"The database did not switch to incremental auto-vacuum (mode {mode}).")
        result = conn.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise CompactionError(f"Integrity check failed after compaction: {result!r}")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise CompactionError("The database is locked by another program; stop it and retry.") from exc
        raise
    finally:
        conn.close()


def compact_database(
    db_path: Path,
    *,
    backup: bool = False,
    confirm: Callable[[DatabaseSpace], bool] | None = None,
    report: Callable[[str], None] = lambda _message: None,
    temp_dir: Path | None = None,
) -> CompactionOutcome:
    """Compact ``db_path`` in place. Preconditions are all checked before anything changes.

    ``confirm`` is shown the current space figures and may decline (nothing is changed). ``backup`` first
    writes a standalone ``..._pre_compact_...back`` snapshot (see ``db/backups.py``).
    """
    db_path = Path(db_path)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return CompactionOutcome("empty")

    lock = acquire_database_lock(db_path)  # raises RuntimeError when a server or another command holds it
    try:
        before = inspect_space(db_path)
        if not needs_compaction(before):
            return CompactionOutcome("already_compact", before=before, after=before)
        check_free_space(db_path, before, backup=backup, temp_dir=temp_dir)
        if confirm is not None and not confirm(before):
            return CompactionOutcome("declined", before=before)

        backup_path = None
        if backup:
            from contextspy.db import migrations
            from contextspy.db.backups import create_backup

            version, _pending = migrations.inspect_migration_state(db_path)
            report("Creating a backup before compacting...")
            backup_path = create_backup(db_path, version, purpose="pre_compact")
            report(f"Backup created: {backup_path}")

        report("Compacting (VACUUM)...")
        started = time.monotonic()
        _vacuum(db_path)
        seconds = time.monotonic() - started
        return CompactionOutcome(
            "compacted", before=before, after=inspect_space(db_path), seconds=seconds, backup_path=backup_path,
        )
    finally:
        release_database_lock(lock)
