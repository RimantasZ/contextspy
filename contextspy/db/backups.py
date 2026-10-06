"""SQLite snapshot creation, discovery, validation, and offline restoration."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock

BackupPurpose = Literal["manual", "pre_restore", "pre_compact", "migration"]


@dataclass(frozen=True)
class RestoreResult:
    backup_path: Path
    restored_version: int
    rollback_path: Path | None


def _stamp(timestamp: datetime | None = None, *, seconds: bool = True) -> str:
    timestamp = timestamp or datetime.now(timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime(
        "%Y-%m-%d-%H%M%SZ" if seconds else "%Y-%m-%d-%H%M"
    )


def backup_name(
    db_path: Path,
    version: int,
    *,
    purpose: BackupPurpose = "manual",
    target_version: int | None = None,
    timestamp: datetime | None = None,
) -> str:
    stem = Path(db_path).stem
    if purpose == "migration":
        if target_version is None:
            raise ValueError("Migration backups require a target version")
        return f"{stem}_backup_v{version}_to_v{target_version}_{_stamp(timestamp, seconds=False)}.back"
    marker = {"pre_restore": "_pre_restore", "pre_compact": "_pre_compact"}.get(purpose, "")
    return f"{stem}_backup_v{version}{marker}_{_stamp(timestamp)}.back"


def _with_suffix(path: Path, sequence: int) -> Path:
    return path if sequence == 0 else path.with_name(f"{path.stem}-{sequence}{path.suffix}")


def _temporary_db(parent: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=".contextspy-backup-", suffix=".tmp", dir=parent)
    os.close(fd)
    return Path(name)


def _source_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _quick_check(path: Path) -> None:
    with closing(_source_connection(path)) as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise ValueError(f"SQLite integrity check failed for {path}: {result!r}")


def _require_standalone_backup(path: Path) -> None:
    # The SQLite header records rollback-journal (1) vs WAL (2) read/write
    # format. A copied WAL-mode main file may omit committed pages in -wal;
    # reject it before SQLite can create sidecars while opening the candidate.
    with path.open("rb") as backup_file:
        header = backup_file.read(20)
    if len(header) < 20 or header[:16] != b"SQLite format 3\0":
        raise ValueError(f"Not a SQLite database: {path}")
    if header[18:20] != b"\x01\x01":
        raise ValueError(
            f"Backup is not a standalone rollback-journal database: {path}. "
            "Use contextspy db-backup to make a complete snapshot."
        )


def _copy_sqlite(source: Path, destination: Path) -> None:
    with closing(_source_connection(source)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)
            # The backup API can copy a WAL-mode header. Convert the completed
            # snapshot to rollback-journal mode so the published .back file is
            # self-contained and read-only inspection needs no sidecars.
            mode = destination_db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if mode != "delete":
                raise RuntimeError(f"Backup could not become standalone: {mode}")


def _remove_temporary_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _check_space(parent: Path, source: Path) -> None:
    required = source.stat().st_size + max(64 * 1024 * 1024, source.stat().st_size // 20)
    available = shutil.disk_usage(parent).free
    if available < required:
        raise RuntimeError(
            f"Not enough free space for a database copy: need at least {required:,} "
            f"bytes, have {available:,} bytes."
        )


def create_backup(
    db_path: Path,
    version: int,
    *,
    purpose: BackupPurpose = "manual",
    target_version: int | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Publish one complete standalone backup without overwriting older files."""
    db_path = Path(db_path)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        raise FileNotFoundError(f"No database to back up at {db_path}")
    parent = db_path.parent
    _check_space(parent, db_path)
    requested = parent / backup_name(
        db_path, version, purpose=purpose,
        target_version=target_version, timestamp=timestamp,
    )
    staged = _temporary_db(parent)
    try:
        _copy_sqlite(db_path, staged)
        _require_standalone_backup(staged)
        _quick_check(staged)
        # Migration snapshots may be from schemas predating the sessions table;
        # their version is determined by the migration caller before this copy.
        if purpose != "migration":
            from contextspy.db import migrations

            snapshot_version, _pending = migrations.inspect_migration_state(staged)
            if snapshot_version != version:
                raise RuntimeError(
                    "Database schema changed during backup creation; "
                    "retry after the migration finishes."
                )
        sequence = 0
        while True:
            candidate = _with_suffix(requested, sequence)
            try:
                # A hard link atomically publishes a completed file and fails
                # rather than replacing an existing backup, even under a race.
                os.link(staged, candidate)
                return candidate
            except FileExistsError:
                sequence += 1
    finally:
        _remove_temporary_db(staged)


def list_backups(db_path: Path) -> list[Path]:
    """List ContextSpy-generated snapshots for this database only."""
    db_path = Path(db_path)
    if not db_path.parent.is_dir():
        return []
    stem = re.escape(db_path.stem)
    patterns = (
        re.compile(rf"(?P<base>{stem}_backup_v\d+_to_v\d+_(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}}-\d{{4}}))(?:-(?P<sequence>\d+))?\.back"),
        re.compile(rf"(?P<base>{stem}_backup_v\d+(?:_pre_restore|_pre_compact)?_(?P<stamp>\d{{4}}-\d{{2}}-\d{{2}}-\d{{6}})Z)(?:-(?P<sequence>\d+))?\.back"),
        re.compile(rf"{stem}_\d+_\d+_\d{{8}}T\d{{12}}Z\.back"),
    )
    matches = [
        path for path in db_path.parent.iterdir()
        if path.is_file() and any(pattern.fullmatch(path.name) for pattern in patterns)
    ]

    def sort_key(path: Path) -> tuple[str, int, str]:
        # Oldest first by the timestamp in the name (the schema version in the name is not
        # ordered as text: "v10" < "v6"); the older HHMM stamps are padded to HHMMSS.
        for pattern in patterns[:2]:
            match = pattern.fullmatch(path.name)
            if match:
                stamp = match.group("stamp")
                return stamp + "00" if len(stamp) == 15 else stamp, int(match.group("sequence") or 0), match.group("base")
        return "", 0, path.name

    return sorted(matches, key=sort_key)


def inspect_backup(path: Path) -> int:
    """Validate a candidate as a complete ContextSpy SQLite database."""
    from contextspy.db import migrations

    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Backup does not exist or is empty: {path}")
    _require_standalone_backup(path)
    _quick_check(path)
    with closing(_source_connection(path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    if not {"requests", "sessions"}.issubset(tables):
        raise ValueError(f"Not a ContextSpy database: {path}")
    version, _pending = migrations.inspect_migration_state(path)
    if version > migrations.SCHEMA_VERSION:
        raise ValueError(
            f"Backup schema v{version} is newer than this ContextSpy build "
            f"(v{migrations.SCHEMA_VERSION})."
        )
    return version


def _checkpoint_and_close(db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(db_path, timeout=0.25)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if mode == "wal":
                busy, _log_pages, _checkpointed = conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if busy:
                    raise RuntimeError("Database has active readers; stop all database users before restore.")
                result = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if result != "delete":
                    raise RuntimeError("Could not make the pre-restore database standalone.")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise RuntimeError("Database is locked; stop all database users before restore.") from exc
        raise
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{db_path}{suffix}").exists():
            raise RuntimeError(
                f"SQLite sidecar {db_path}{suffix} remains. Stop all database users "
                "and retry; the restore will not replace the database while it exists."
            )


def restore_backup(
    db_path: Path,
    backup_path: Path,
    *,
    timestamp: datetime | None = None,
    validated_version: int | None = None,
) -> RestoreResult:
    """Restore a validated backup while preserving the former DB for rollback.

    The caller must additionally check for an older running ContextSpy process,
    which would not hold this version's maintenance lock.
    """
    from contextspy.db import migrations

    db_path = Path(db_path)
    backup_path = Path(backup_path)
    if backup_path.resolve() == db_path.resolve() or (
        db_path.exists() and backup_path.exists() and os.path.samefile(backup_path, db_path)
    ):
        raise ValueError("The backup and active database are the same file")
    lock = acquire_database_lock(db_path)
    staged: Path | None = None
    try:
        _require_standalone_backup(backup_path)
        version = validated_version if validated_version is not None else inspect_backup(backup_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _check_space(db_path.parent, backup_path)
        staged = _temporary_db(db_path.parent)
        _copy_sqlite(backup_path, staged)
        if inspect_backup(staged) != version:
            raise RuntimeError("The backup changed while the restore was being staged.")
        _checkpoint_and_close(db_path)

        rollback_path = None
        if db_path.exists():
            current_version, _pending = migrations.inspect_migration_state(db_path)
            requested = db_path.parent / backup_name(
                db_path, current_version, purpose="pre_restore", timestamp=timestamp,
            )
            sequence = 0
            while _with_suffix(requested, sequence).exists():
                sequence += 1
            rollback_path = _with_suffix(requested, sequence)
            # A stopped, checkpointed database is self-contained. Keep the
            # former file as the rollback artifact without copying gigabytes.
            db_path.rename(rollback_path)
        try:
            staged.replace(db_path)
            staged = None
        except Exception as swap_error:
            if rollback_path is not None:
                try:
                    rollback_path.rename(db_path)
                except OSError as rollback_error:
                    raise RuntimeError(
                        f"Restore swap failed ({swap_error}); automatic rollback also failed "
                        f"({rollback_error}). The previous database is preserved at "
                        f"{rollback_path}; restore it manually before starting ContextSpy."
                    ) from rollback_error
            raise
        return RestoreResult(backup_path, version, rollback_path)
    finally:
        if staged is not None:
            _remove_temporary_db(staged)
        release_database_lock(lock)
