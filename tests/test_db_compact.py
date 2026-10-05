"""`contextspy db-compact`: reclaiming free pages, incremental auto-vacuum, and backups that survive it."""
import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextspy import cli
from contextspy.config import Settings
from contextspy.db import backups, compaction, database
from contextspy.db.compaction import CompactionError, DatabaseSpace, compact_database, inspect_space
from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock

runner = CliRunner()


def auto_vacuum(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA auto_vacuum").fetchone()[0]


def legacy_db(path: Path, *, rows: int = 400, payload: int = 20_000, keep_every: int = 4) -> Path:
    """A pre-incremental database (auto_vacuum off) whose deleted rows left free pages behind."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA auto_vacuum = 0")
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY);"
        "CREATE TABLE requests (id TEXT PRIMARY KEY);"
        "CREATE TABLE filler (id INTEGER PRIMARY KEY, body TEXT);"
    )
    conn.executemany("INSERT INTO filler (body) VALUES (?)", [("x" * payload,)] * rows)
    conn.commit()
    conn.execute("DELETE FROM filler WHERE id % ? != 0", (keep_every,))
    conn.commit()
    conn.close()
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filler_rows(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM filler").fetchone()[0]


# --------------------------------------------------------------------------- space accounting

def space(**overrides) -> DatabaseSpace:
    values = dict(page_size=4096, page_count=1000, freelist_count=500, auto_vacuum=0, file_bytes=4096 * 1000, wal_bytes=0)
    return DatabaseSpace(**{**values, **overrides})


def test_space_figures():
    s = space()
    assert (s.free_bytes, s.live_bytes, s.free_fraction, s.incremental) == (500 * 4096, 500 * 4096, 0.5, False)
    assert space(page_count=0, freelist_count=0).free_fraction == 0.0


def test_needs_compaction_rules():
    assert compaction.needs_compaction(space(auto_vacuum=0, freelist_count=0))       # conversion alone is a reason
    assert not compaction.needs_compaction(space(auto_vacuum=2, freelist_count=0))


def test_incremental_files_with_little_free_space_are_left_alone():
    tiny_free = space(auto_vacuum=2, page_count=100_000, freelist_count=1_000, file_bytes=4096 * 100_000)
    assert tiny_free.free_bytes < 16 * 1024 * 1024 and not compaction.needs_compaction(tiny_free)
    lots = space(auto_vacuum=2, page_count=100_000, freelist_count=60_000, file_bytes=4096 * 100_000)
    assert compaction.needs_compaction(lots)


def test_required_free_bytes_adds_backup_and_temp_volume_requirements():
    s = space()
    base = compaction.required_free_bytes(s)
    assert base == int(s.live_bytes * 1.25) + 64 * 1024 * 1024
    assert compaction.required_free_bytes(s, backup=True) > base + s.file_bytes
    assert compaction.required_free_bytes(s, temp_on_same_volume=True) == base + int(s.live_bytes * 1.1)


# --------------------------------------------------------------------------- compaction

def test_compaction_shrinks_the_file_keeps_the_rows_and_enables_incremental_mode(tmp_path):
    path = legacy_db(tmp_path / "legacy.db")
    before = path.stat().st_size
    assert auto_vacuum(path) == 0 and inspect_space(path).freelist_count > 0

    outcome = compact_database(path)

    assert outcome.status == "compacted"
    assert path.stat().st_size < before / 2
    assert filler_rows(path) == 100
    assert auto_vacuum(path) == 2 and inspect_space(path).freelist_count == 0
    assert outcome.reclaimed_bytes > 0 and outcome.before.file_bytes == before
    assert not Path(f"{path}-wal").exists() or Path(f"{path}-wal").stat().st_size == 0


def test_a_second_run_has_nothing_to_do_and_does_not_vacuum(tmp_path, monkeypatch):
    path = legacy_db(tmp_path / "legacy.db")
    compact_database(path)
    monkeypatch.setattr(compaction, "_vacuum", lambda p: pytest.fail("VACUUM must not run again"))
    assert compact_database(path).status == "already_compact"


def test_missing_or_empty_database_is_reported_not_created(tmp_path):
    assert compact_database(tmp_path / "missing.db").status == "empty"
    (tmp_path / "empty.db").write_bytes(b"")
    assert compact_database(tmp_path / "empty.db").status == "empty"
    assert not (tmp_path / "missing.db").exists()


def test_refuses_while_the_maintenance_lock_is_held_and_changes_nothing(tmp_path):
    path = legacy_db(tmp_path / "legacy.db")
    original = digest(path)
    lock = acquire_database_lock(path)
    try:
        with pytest.raises(RuntimeError, match="in use"):
            compact_database(path)
    finally:
        release_database_lock(lock)
    assert digest(path) == original


def test_insufficient_disk_space_aborts_before_touching_the_file(tmp_path, monkeypatch):
    path = legacy_db(tmp_path / "legacy.db")
    original = digest(path)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(1_000_000, 999_000, 1_000))
    with pytest.raises(CompactionError, match="Not enough free disk space"):
        compact_database(path)
    assert digest(path) == original
    release_database_lock(acquire_database_lock(path))  # the lock was released again


def test_the_temp_directory_volume_is_checked_when_it_differs(tmp_path, monkeypatch):
    path = legacy_db(tmp_path / "legacy.db")
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(compaction, "_same_volume", lambda a, b: False)
    real = shutil.disk_usage

    def usage(p):
        return shutil._ntuple_diskusage(10**9, 10**9, 1_000) if Path(p) == temp else real(p)

    monkeypatch.setattr(shutil, "disk_usage", usage)
    with pytest.raises(CompactionError, match="temporary directory"):
        compact_database(path, temp_dir=temp)


def test_a_failed_vacuum_leaves_the_database_intact_and_the_lock_released(tmp_path, monkeypatch):
    path = legacy_db(tmp_path / "legacy.db")
    original = digest(path)

    def boom(_path):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(compaction, "_vacuum", boom)
    with pytest.raises(sqlite3.OperationalError):
        compact_database(path)
    assert digest(path) == original
    release_database_lock(acquire_database_lock(path))


def test_active_connections_are_reported_as_a_busy_database(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction, "_BUSY_TIMEOUT_SECONDS", 0.2)
    path = legacy_db(tmp_path / "legacy.db")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")  # an open write transaction on the file
    try:
        with pytest.raises((CompactionError, sqlite3.OperationalError)):
            compact_database(path)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert filler_rows(path) == 100


def test_confirmation_can_decline(tmp_path):
    path = legacy_db(tmp_path / "legacy.db")
    original = digest(path)
    seen = []
    outcome = compact_database(path, confirm=lambda s: seen.append(s) or False)
    assert outcome.status == "declined" and seen and seen[0].freelist_count > 0
    assert digest(path) == original


# --------------------------------------------------------------------------- new databases

def test_new_databases_start_in_incremental_mode_and_existing_ones_are_untouched(tmp_path):
    fresh = tmp_path / "fresh.db"
    database.init_db(fresh)
    database.dispose_engine()
    assert auto_vacuum(fresh) == 2
    with sqlite3.connect(fresh) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    # An existing full-schema database that is not in incremental mode stays that way.
    old = tmp_path / "old.db"
    database.init_db(old)
    database.dispose_engine()
    conn = sqlite3.connect(old)
    conn.execute("PRAGMA auto_vacuum = NONE")
    conn.execute("VACUUM")
    conn.close()
    assert auto_vacuum(old) == 0
    database.init_db(old)
    database.dispose_engine()
    assert auto_vacuum(old) == 0


def test_deleted_content_is_returned_to_the_file_by_incremental_vacuum(tmp_path):
    path = tmp_path / "fresh.db"
    database.init_db(path)
    database.dispose_engine()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE blob (id INTEGER PRIMARY KEY, body TEXT)")
        conn.executemany("INSERT INTO blob (body) VALUES (?)", [("y" * 50_000,)] * 200)
        conn.commit()
        conn.execute("DELETE FROM blob")
        conn.commit()
        grown = inspect_space(path)
        assert grown.freelist_count > 0
        conn.execute("PRAGMA incremental_vacuum").fetchall()  # rows must be fetched for it to do any work
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    assert inspect_space(path).freelist_count == 0
    assert path.stat().st_size < grown.file_bytes


# --------------------------------------------------------------------------- backups and restore

def test_backup_names_and_discovery_include_pre_compact(tmp_path):
    db_path = tmp_path / "contextspy.db"
    stamp = datetime(2026, 10, 6, 9, 30, 0, tzinfo=timezone.utc)
    name = backups.backup_name(db_path, 9, purpose="pre_compact", timestamp=stamp)
    assert name == "contextspy_backup_v9_pre_compact_2026-10-06-093000Z.back"
    (tmp_path / name).write_bytes(b"x")
    (tmp_path / "contextspy_backup_v9_pre_compact_2026-10-06-093000Z-1.back").write_bytes(b"x")
    assert [p.name for p in backups.list_backups(db_path)] == [
        name, "contextspy_backup_v9_pre_compact_2026-10-06-093000Z-1.back",
    ]


def test_existing_backups_survive_compaction_and_still_restore(tmp_path):
    from contextspy.db import migrations

    live = legacy_db(tmp_path / "contextspy.db")
    version, _pending = migrations.inspect_migration_state(live)
    old_backup = backups.create_backup(live, version)
    backup_bytes = digest(old_backup)

    compact_database(live)

    assert digest(old_backup) == backup_bytes                      # the compaction never touches a backup
    assert old_backup in backups.list_backups(live)
    assert backups.inspect_backup(old_backup) == version

    backups.restore_backup(live, old_backup)
    assert filler_rows(live) == 100
    assert auto_vacuum(live) == 0                                   # restored as it was: not compacted, not incremental
    assert inspect_space(live).freelist_count > 0
    compact_database(live)                                          # and it can be compacted again
    assert auto_vacuum(live) == 2


def test_the_pre_compaction_backup_is_listed_and_restorable(tmp_path):
    live = legacy_db(tmp_path / "contextspy.db")
    outcome = compact_database(live, backup=True)

    assert outcome.backup_path is not None and "_pre_compact_" in outcome.backup_path.name
    assert outcome.backup_path in backups.list_backups(live)
    assert backups.inspect_backup(outcome.backup_path) >= 1
    after = live.stat().st_size
    backups.restore_backup(live, outcome.backup_path)
    assert filler_rows(live) == 100 and auto_vacuum(live) == 0 and live.stat().st_size > after


def test_a_backup_adds_its_own_space_requirement(tmp_path, monkeypatch):
    live = legacy_db(tmp_path / "contextspy.db")
    current = inspect_space(live)
    monkeypatch.setattr(compaction, "_same_volume", lambda a, b: True)
    enough_without_backup = compaction.required_free_bytes(current, temp_on_same_volume=True) + 1
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(10**12, 10**12 - enough_without_backup, enough_without_backup))
    compaction.check_free_space(live, current, backup=False)
    with pytest.raises(CompactionError, match="Not enough free disk space"):
        compaction.check_free_space(live, current, backup=True)


# --------------------------------------------------------------------------- CLI

@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = legacy_db(tmp_path / "contextspy.db")
    settings = Settings(config_dir=tmp_path)
    settings.storage.db_path = path
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda s: False)
    return path


def test_cli_compacts_with_yes(configured):
    before = configured.stat().st_size
    result = runner.invoke(cli.app, ["db-compact", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Done" in result.output and "incremental auto-vacuum is now enabled" in result.output.lower()
    assert configured.stat().st_size < before and auto_vacuum(configured) == 2


def test_cli_declined_prompt_changes_nothing(configured):
    original = digest(configured)
    result = runner.invoke(cli.app, ["db-compact"], input="n\n")
    assert result.exit_code == 0 and "Nothing changed" in result.output
    assert digest(configured) == original


def test_cli_confirmed_prompt_compacts(configured):
    result = runner.invoke(cli.app, ["db-compact"], input="y\n")
    assert result.exit_code == 0 and auto_vacuum(configured) == 2


def test_cli_refuses_while_the_backend_is_running(configured, monkeypatch):
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda s: True)
    original = digest(configured)
    result = runner.invoke(cli.app, ["db-compact", "--yes"])
    assert result.exit_code == 1 and "Stop ContextSpy" in result.output
    assert digest(configured) == original


def test_cli_reports_a_held_lock_and_a_missing_database(configured, tmp_path):
    lock = acquire_database_lock(configured)
    try:
        result = runner.invoke(cli.app, ["db-compact", "--yes"])
    finally:
        release_database_lock(lock)
    assert result.exit_code == 1 and "in use" in result.output
    configured.unlink()
    result = runner.invoke(cli.app, ["db-compact", "--yes"])
    assert result.exit_code == 0 and "No database to compact" in result.output


def test_cli_already_compact_and_backup_option(configured):
    assert runner.invoke(cli.app, ["db-compact", "--yes", "--backup"]).exit_code == 0
    assert list(configured.parent.glob("*_pre_compact_*.back"))
    again = runner.invoke(cli.app, ["db-compact", "--yes"])
    assert again.exit_code == 0 and "already compact" in again.output.lower()


def test_db_stats_reports_size_free_space_and_suggests_compaction(configured):
    result = runner.invoke(cli.app, ["db-stats"])
    assert result.exit_code == 0, result.output
    assert "File size:" in result.output and "auto-vacuum: off" in result.output
    assert "db-compact" in result.output
    runner.invoke(cli.app, ["db-compact", "--yes"])
    after = runner.invoke(cli.app, ["db-stats"])
    assert "auto-vacuum: incremental" in after.output and "db-compact" not in after.output
