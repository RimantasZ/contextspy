"""Cross-process lock guarding replacement of a ContextSpy database file.

The server holds this lock for its lifetime. Offline maintenance commands use
the same lock, so a restore cannot swap files under a running new-version server.
The lock file is intentionally persistent; the operating system releases its
lock when a process exits, including after a crash.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


def acquire_database_lock(db_path: Path) -> BinaryIO:
    # Resolve aliases so a relative path and a symlink to the same database
    # cannot obtain separate maintenance locks.
    db_path = Path(db_path).resolve()
    lock_path = Path(f"{db_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            f"Database {db_path} is in use. Stop ContextSpy and other maintenance "
            "commands before restoring or upgrading it."
        ) from exc
    return handle


def release_database_lock(handle: BinaryIO) -> None:
    if handle.closed:
        return
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
