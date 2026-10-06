"""On-demand, migration, and pre-restore SQLite backup behavior."""
from __future__ import annotations

import sqlite3
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contextspy import cli
from contextspy.config import Settings
from contextspy.db import backups, database, migrations

SCHEMA = migrations.SCHEMA_VERSION
# Data migrations a database restored from a v6 backup still needs (v7 onwards).
PENDING_LIST = list(range(7, SCHEMA + 1))


def _database(path):
    database.init_db(path)
    with database.get_db() as db:
        migrations.check_and_flag_pending_migrations(db)
    database.dispose_engine()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE backup_marker (value TEXT)")
        conn.execute("INSERT INTO backup_marker VALUES ('original')")
        conn.commit()
    return path


def _markers(path):
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT value FROM backup_marker ORDER BY rowid")]


def test_backup_names_distinguish_purpose_and_version(tmp_path):
    db_path = tmp_path / "contextspy.db"
    timestamp = datetime(2026, 9, 30, 18, 15, 4, tzinfo=timezone.utc)
    assert backups.backup_name(db_path, 7, timestamp=timestamp) == (
        "contextspy_backup_v7_2026-09-30-181504Z.back"
    )
    assert backups.backup_name(db_path, 7, purpose="pre_restore", timestamp=timestamp) == (
        "contextspy_backup_v7_pre_restore_2026-09-30-181504Z.back"
    )
    assert backups.backup_name(
        db_path, 6, purpose="migration", target_version=7, timestamp=timestamp,
    ) == "contextspy_backup_v6_to_v7_2026-09-30-1815.back"


def test_manual_backup_includes_uncheckpointed_wal_and_is_standalone(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    source = sqlite3.connect(path)
    try:
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("INSERT INTO backup_marker VALUES ('from-wal')")
        source.commit()
        backup = backups.create_backup(path, SCHEMA)
        assert backup.name.startswith(f"contextspy_backup_v{SCHEMA}_")
        assert backup.suffix == ".back"
        assert _markers(backup) == ["original", "from-wal"]
        assert backups.inspect_backup(backup) == SCHEMA
        assert not (tmp_path / f"{backup.name}-wal").exists()
        assert not (tmp_path / f"{backup.name}-shm").exists()
    finally:
        source.close()


def test_backup_collision_and_failed_copy_never_replace_existing(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    timestamp = datetime(2026, 9, 30, 18, 15, 4, tzinfo=timezone.utc)
    first = backups.create_backup(path, SCHEMA, timestamp=timestamp)
    second = backups.create_backup(path, SCHEMA, timestamp=timestamp)
    assert second.name == f"contextspy_backup_v{SCHEMA}_2026-09-30-181504Z-1.back"

    def fail_copy(_source, destination):
        destination.write_bytes(b"incomplete")
        raise OSError("interrupted")

    monkeypatch.setattr(backups, "_copy_sqlite", fail_copy)
    with pytest.raises(OSError, match="interrupted"):
        backups.create_backup(path, SCHEMA, timestamp=timestamp)
    assert _markers(first) == ["original"]
    assert _markers(second) == ["original"]
    assert not list(tmp_path.glob(".contextspy-backup-*.tmp"))


def test_list_backups_includes_manual_restore_and_migration_only_for_db(tmp_path):
    path = tmp_path / "profile.db"
    names = [
        "profile_backup_v6_to_v7_2026-09-30-1445.back",
        "profile_backup_v7_2026-09-30-181504Z.back",
        "profile_backup_v7_pre_restore_2026-09-30-181505Z.back",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"backup")
    (tmp_path / "other_backup_v7_2026-09-30-181504Z.back").write_bytes(b"other")
    assert {file.name for file in backups.list_backups(path)} == set(names)
    assert [file.name for file in migrations.list_migration_backups(path)] == names[:1]


def test_restore_preserves_current_database_without_changing_source(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO backup_marker VALUES ('newer')")
        conn.commit()
    result = backups.restore_backup(
        path, backup,
        timestamp=datetime(2026, 9, 30, 18, 20, 1, tzinfo=timezone.utc),
    )
    assert _markers(path) == ["original"]
    assert _markers(backup) == ["original"]
    assert result.rollback_path.name == (
        f"contextspy_backup_v{SCHEMA}_pre_restore_2026-09-30-182001Z.back"
    )
    assert _markers(result.rollback_path) == ["original", "newer"]
    assert backups.inspect_backup(result.rollback_path) == SCHEMA
    database.init_db(path)
    with database.get_engine().connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
    database.dispose_engine()


def test_restore_refuses_running_database_and_keeps_files(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    database.init_db(path)
    try:
        with pytest.raises(RuntimeError, match="in use"):
            backups.restore_backup(path, backup)
        assert _markers(path) == ["original"]
        assert _markers(backup) == ["original"]
    finally:
        database.dispose_engine()


def test_restore_refuses_another_open_sqlite_connection(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    reader = sqlite3.connect(path)
    reader.execute("SELECT COUNT(*) FROM requests").fetchone()
    try:
        with pytest.raises(RuntimeError, match="stop all database users|locked|sidecar"):
            backups.restore_backup(path, backup)
        assert _markers(path) == ["original"]
    finally:
        reader.close()


def test_restore_swap_failure_puts_current_database_back(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO backup_marker VALUES ('newer')")
        conn.commit()
    original_replace = Path.replace

    def fail_staged_replace(source, destination):
        if source.name.startswith(".contextspy-backup-"):
            raise OSError("swap failed")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_staged_replace)
    with pytest.raises(OSError, match="swap failed"):
        backups.restore_backup(path, backup)
    assert _markers(path) == ["original", "newer"]
    assert _markers(backup) == ["original"]
    assert not list(tmp_path.glob("*pre_restore*.back"))
    assert not list(tmp_path.glob(".contextspy-backup-*.tmp"))


def test_restore_reports_rollback_location_if_automatic_rollback_fails(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    original_rename = Path.rename

    def fail_swap_and_rollback(source, destination):
        if source.name.startswith(".contextspy-backup-"):
            raise OSError("swap failed")
        return original_rename(source, destination)

    def fail_rollback(source, destination):
        if "pre_restore" in source.name:
            raise OSError("rollback failed")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "replace", fail_swap_and_rollback)
    monkeypatch.setattr(Path, "rename", fail_rollback)
    with pytest.raises(RuntimeError, match="previous database is preserved at"):
        backups.restore_backup(path, backup)
    assert not path.exists()
    assert len(list(tmp_path.glob("*pre_restore*.back"))) == 1
    assert _markers(backup) == ["original"]


def test_cli_backup_and_restore_dry_run_and_confirmed(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    settings = Settings(config_dir=tmp_path)
    settings.storage.db_path = path
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda _settings: False)
    runner = CliRunner()

    created = runner.invoke(cli.app, ["db-backup"])
    assert created.exit_code == 0, created.output
    backup = backups.list_backups(path)[0]
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO backup_marker VALUES ('newer')")
        conn.commit()

    preview = runner.invoke(cli.app, ["db-restore", backup.name, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "no files changed" in preview.output
    assert _markers(path) == ["original", "newer"]

    restored = runner.invoke(cli.app, ["db-restore", backup.name, "--yes"])
    assert restored.exit_code == 0, restored.output
    assert "Previous database preserved" in restored.output
    assert _markers(path) == ["original"]
    assert len(backups.list_backups(path)) == 2


def test_cli_restore_older_schema_reports_required_migration(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE schema_meta SET value = '6' WHERE key = 'schema_version'")
        conn.commit()
    backup = migrations.create_migration_backup(path, 6, SCHEMA)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"UPDATE schema_meta SET value = '{SCHEMA}' WHERE key = 'schema_version'")
        conn.commit()
    settings = Settings(config_dir=tmp_path)
    settings.storage.db_path = path
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda _settings: False)

    result = CliRunner().invoke(cli.app, ["db-restore", backup.name, "--yes"])
    assert result.exit_code == 0, result.output
    assert f"Data migrations required after restore: {PENDING_LIST}" in result.output
    assert "contextspy db-upgrade" in result.output
    assert migrations.inspect_migration_state(path) == (6, PENDING_LIST)
    assert backups.inspect_backup(backups.list_backups(path)[-1]) == SCHEMA


def test_cli_status_lists_backups_even_when_server_is_offline(tmp_path, monkeypatch):
    path = _database(tmp_path / "contextspy.db")
    backup = backups.create_backup(path, SCHEMA)
    settings = Settings(config_dir=tmp_path)
    settings.storage.db_path = path
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli.httpx, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ConnectionError("offline")
    ))
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0, result.output
    assert "Web server not reachable" in result.output
    assert "Previous database backups" in result.output
    assert backup.name[-10:] in result.output.replace("\n", "")


def test_restore_rejects_non_contextspy_or_newer_database(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    wrong = tmp_path / "wrong.back"
    with closing(sqlite3.connect(wrong)) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
        conn.commit()
    with pytest.raises(ValueError, match="Not a ContextSpy database"):
        backups.restore_backup(path, wrong)
    assert _markers(path) == ["original"]

    too_new = backups.create_backup(path, SCHEMA)
    with closing(sqlite3.connect(too_new)) as conn:
        conn.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
    with pytest.raises(ValueError, match="newer"):
        backups.restore_backup(path, too_new)
    assert _markers(path) == ["original"]


def test_restore_rejects_copied_wal_main_file(tmp_path):
    path = _database(tmp_path / "contextspy.db")
    source = sqlite3.connect(path)
    try:
        source.execute("INSERT INTO backup_marker VALUES ('not-checkpointed')")
        source.commit()
        unsafe = tmp_path / "copied-main-only.back"
        shutil.copy2(path, unsafe)
        with pytest.raises(ValueError, match="not a standalone"):
            backups.restore_backup(path, unsafe)
        assert _markers(path) == ["original", "not-checkpointed"]
    finally:
        source.close()


def test_list_backups_orders_by_timestamp_not_by_schema_version_text(tmp_path):
    db = tmp_path / "contextspy.db"
    older = tmp_path / "contextspy_backup_v6_to_v10_2026-10-06-0521.back"
    newer = tmp_path / "contextspy_backup_v10_pre_restore_2026-10-06-052130Z.back"
    newest = tmp_path / "contextspy_backup_v9_2026-10-07-010101Z.back"
    for path in (newest, newer, older):
        path.write_bytes(b"x")
    # "v10" sorts before "v6" and "v9" as text; the order must still be oldest first.
    assert backups.list_backups(db) == [older, newer, newest]
