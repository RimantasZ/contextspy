"""Swap the active database with a shelved one (``contextspy db-switch``).

Layout, next to the configured database::

    contextspy.db, contextspy_backup_*.back      the active database and its backups
    db_switch/.active                            name of the active database
    db_switch/<name>/contextspy.db + *.back      shelved databases (standalone files)

Only the ``.db`` file and its own ``.back`` files are moved, never SQLite sidecars: the outgoing
database is checkpointed and converted out of WAL first, so a shelved database is one file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from contextspy.db.backups import _checkpoint_and_close, inspect_backup, list_backups
from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock

SHELF_DIRNAME = "db_switch"
ACTIVE_FILENAME = ".active"
DEFAULT_NAME = "default"
STAGING_PREFIX = ".swap-"
_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")
_SIDECARS = ("-wal", "-shm", "-journal")


class SwitchError(Exception):
    """A switch was refused or failed; the message is meant for the user."""


@dataclass(frozen=True)
class SwitchResult:
    previous_name: str
    new_name: str
    created: bool
    moved_out: int
    moved_in: int


@dataclass(frozen=True)
class DatabaseEntry:
    name: str
    active: bool
    db_bytes: int
    backups: int


def shelf_dir(db_path: Path) -> Path:
    return Path(db_path).parent / SHELF_DIRNAME


def validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise SwitchError(
            f"Invalid database name {name!r}: use letters, digits, '_' and '-' "
            "(max 64 characters, not starting with '-')."
        )
    return name


def active_name(db_path: Path) -> str | None:
    """Recorded name of the active database; ``None`` if it was never switched."""
    try:
        name = (shelf_dir(db_path) / ACTIVE_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name if _NAME_RE.fullmatch(name) else None


def _write_active(db_path: Path, name: str) -> None:
    shelf = shelf_dir(db_path)
    shelf.mkdir(exist_ok=True)
    tmp = shelf / f"{ACTIVE_FILENAME}.tmp"
    tmp.write_text(name + "\n", encoding="utf-8")
    os.replace(tmp, shelf / ACTIVE_FILENAME)  # our own marker; replacing it is intended


def _slots(db_path: Path) -> list[Path]:
    shelf = shelf_dir(db_path)
    if not shelf.is_dir():
        return []
    return sorted(p for p in shelf.iterdir() if p.is_dir() and not p.name.startswith("."))


def _file_set(db_path: Path) -> list[Path]:
    """The database file plus its own backups, in the directory of ``db_path``."""
    files = [db_path] if db_path.is_file() else []
    return files + list_backups(db_path)


def list_databases(db_path: Path) -> list[DatabaseEntry]:
    db_path = Path(db_path)
    entries: list[DatabaseEntry] = []
    current = active_name(db_path) or DEFAULT_NAME
    if db_path.is_file():
        entries.append(DatabaseEntry(current, True, db_path.stat().st_size, len(list_backups(db_path))))
    for slot in _slots(db_path):
        slot_db = slot / db_path.name
        entries.append(DatabaseEntry(
            slot.name, False,
            slot_db.stat().st_size if slot_db.is_file() else 0,
            len(list_backups(slot_db)),
        ))
    return entries


def _move(src: Path, dst: Path, journal: list[tuple[Path, Path]]) -> None:
    # os.rename silently replaces on POSIX, so check first. The maintenance lock excludes other
    # ContextSpy processes while we do.
    if os.path.lexists(dst):
        raise SwitchError(f"Refusing to overwrite existing file: {dst}")
    os.rename(src, dst)
    journal.append((src, dst))


def _rollback(journal: list[tuple[Path, Path]]) -> list[str]:
    problems: list[str] = []
    for src, dst in reversed(journal):
        try:
            if os.path.lexists(src):
                raise SwitchError(f"{src} exists")
            src.parent.mkdir(exist_ok=True)  # a slot or staging dir may already be removed
            os.rename(dst, src)
        except (OSError, SwitchError) as exc:
            problems.append(f"{dst} -> {src}: {exc}")
    return problems


def _check_case_collision(slots: list[Path], name: str) -> None:
    for slot in slots:
        if slot.name != name and slot.name.lower() == name.lower():
            raise SwitchError(
                f"Name {name!r} differs only by case from the existing database {slot.name!r}; "
                "this would collide on case-insensitive filesystems."
            )


def switch_database(
    db_path: Path,
    target: str,
    *,
    save_as: str | None = None,
    create: bool = False,
) -> SwitchResult:
    """Shelve the active database and bring ``target`` (or a fresh, empty one) into place.

    The caller must additionally make sure no older ContextSpy build is running.
    """
    db_path = Path(db_path)
    validate_name(target)
    if save_as is not None:
        validate_name(save_as)
    shelf = shelf_dir(db_path)
    leftovers = sorted(shelf.glob(f"{STAGING_PREFIX}*")) if shelf.is_dir() else []
    if leftovers:
        raise SwitchError(
            f"An earlier switch did not finish: {leftovers[0]} still exists. Its files belong to "
            "the databases involved; move them back by hand, then remove the directory."
        )

    current = save_as or active_name(db_path) or DEFAULT_NAME
    if current.lower() == target.lower():
        raise SwitchError(
            f"{target!r} is already the active database."
            if current == target and save_as is None
            else f"Cannot shelve the active database as {current!r}: it collides with {target!r}."
        )
    slots = _slots(db_path)
    by_name = {slot.name: slot for slot in slots}
    target_slot = by_name.get(target)
    if create:
        if target_slot is not None:
            raise SwitchError(f"Database {target!r} already exists; omit --new to switch to it.")
        _check_case_collision(slots, target)
    elif target_slot is None:
        known = ", ".join(sorted(by_name)) or "none"
        raise SwitchError(
            f"No shelved database named {target!r} (shelved: {known}). Use --new to start an empty one."
        )
    out_slot = shelf / current
    _check_case_collision(slots, current)
    if current in by_name and any(out_slot.iterdir()):
        raise SwitchError(
            f"A shelved database named {current!r} already exists. Name the active one differently "
            "with --as."
        )

    lock = acquire_database_lock(db_path)
    journal: list[tuple[Path, Path]] = []
    try:
        incoming: list[Path] = []
        if target_slot is not None:
            incoming_db = target_slot / db_path.name
            for suffix in _SIDECARS:
                if Path(f"{incoming_db}{suffix}").exists():
                    raise SwitchError(
                        f"Shelved database {target!r} has an SQLite sidecar ({incoming_db.name}{suffix}); "
                        "it was not shelved by db-switch. Remove the sidecar only if you are sure the "
                        "database was cleanly closed."
                    )
            if not incoming_db.is_file():
                raise SwitchError(f"Shelved database {target!r} has no {db_path.name}.")
            try:
                inspect_backup(incoming_db)
            except (OSError, RuntimeError, ValueError) as exc:
                raise SwitchError(f"Shelved database {target!r} is not usable: {exc}") from exc
            incoming = _file_set(incoming_db)

        try:
            _checkpoint_and_close(db_path)
        except (OSError, RuntimeError) as exc:
            raise SwitchError(str(exc)) from exc
        outgoing = _file_set(db_path)

        shelf.mkdir(exist_ok=True)
        staging = shelf / f"{STAGING_PREFIX}{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
        staging.mkdir()
        try:
            for path in outgoing:
                _move(path, staging / path.name, journal)
            for path in incoming:
                _move(path, db_path.parent / path.name, journal)
            if outgoing:
                out_slot.mkdir(exist_ok=True)
                for path in outgoing:
                    _move(staging / path.name, out_slot / path.name, journal)
            staging.rmdir()
            if target_slot is not None:
                try:
                    target_slot.rmdir()  # only if now empty; unrelated files keep it alive
                except OSError:
                    pass
            _write_active(db_path, target)
        except BaseException:
            problems = _rollback(journal)
            if problems:
                raise SwitchError(
                    "Switch failed and could not be fully undone. Files may be in "
                    f"{staging} and the shelf; undo by hand: " + "; ".join(problems)
                ) from None
            if staging.exists():
                try:
                    staging.rmdir()
                except OSError:
                    pass
            raise
        return SwitchResult(current, target, create, len(outgoing), len(incoming))
    finally:
        release_database_lock(lock)
