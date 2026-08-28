import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from contextspy.db import migrations


def _legacy_db_with_request(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE requests (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO requests (id) VALUES ('old-request')")


def test_inspect_migration_state_is_read_only_for_legacy_database(tmp_path):
    db_path = tmp_path / "contextspy.db"
    _legacy_db_with_request(db_path)
    original_bytes = db_path.read_bytes()

    version_from, pending = migrations.inspect_migration_state(db_path)

    assert version_from == 1
    assert pending == [2]
    assert db_path.read_bytes() == original_bytes
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert tables == {"requests"}


def test_create_migration_backup_uses_versioned_filename_and_exact_copy(tmp_path):
    db_path = tmp_path / "contextspy.db"
    _legacy_db_with_request(db_path)
    timestamp = datetime(2026, 8, 27, 12, 34, 56, 789, tzinfo=timezone.utc)

    backup_path = migrations.create_migration_backup(
        db_path, 1, 2, timestamp=timestamp
    )

    assert backup_path.name == "contextspy_backup_v1_to_v2_2026-08-27-1234.back"
    assert backup_path.read_bytes() == db_path.read_bytes()


def test_create_migration_backup_adds_suffix_when_name_exists(tmp_path):
    db_path = tmp_path / "contextspy.db"
    _legacy_db_with_request(db_path)
    timestamp = datetime(2026, 8, 27, 12, 34, tzinfo=timezone.utc)
    base = tmp_path / "contextspy_backup_v1_to_v2_2026-08-27-1234.back"
    suffixed = tmp_path / "contextspy_backup_v1_to_v2_2026-08-27-1234-1.back"
    base.write_bytes(b"first backup")
    suffixed.write_bytes(b"second backup")

    backup_path = migrations.create_migration_backup(
        db_path, 1, 2, timestamp=timestamp
    )

    assert backup_path.name == "contextspy_backup_v1_to_v2_2026-08-27-1234-2.back"
    assert backup_path.read_bytes() == db_path.read_bytes()
    assert base.read_bytes() == b"first backup"
    assert suffixed.read_bytes() == b"second backup"


def test_db_upgrade_copies_database_before_initialization(monkeypatch, tmp_path):
    from contextspy import cli
    from contextspy.config import Settings
    from contextspy.db import database

    db_path = tmp_path / "profile.db"
    original_bytes = b"untouched sqlite database"
    db_path.write_bytes(original_bytes)
    previous_backup = tmp_path / "profile_backup_v0_to_v1_2026-08-26-0000.back"
    previous_backup.write_bytes(b"old backup")

    settings = Settings(config_dir=tmp_path)
    settings.storage.db_path = db_path
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(migrations, "inspect_migration_state", lambda path: (1, [2]))

    events = []
    real_create_backup = migrations.create_migration_backup

    def create_backup(path, version_from, version_to):
        events.append("backup")
        return real_create_backup(
            path,
            version_from,
            version_to,
            timestamp=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

    def init_db(path):
        events.append("init")
        path.write_bytes(b"database changed by init")

    @contextmanager
    def get_db():
        yield object()

    monkeypatch.setattr(migrations, "create_migration_backup", create_backup)
    monkeypatch.setattr(migrations, "check_and_flag_pending_migrations", lambda db: [2])
    monkeypatch.setattr(migrations, "apply_data_migrations", lambda db: [2])
    monkeypatch.setattr(database, "init_db", init_db)
    monkeypatch.setattr(database, "get_db", get_db)

    output = []

    class RecordingConsole:
        def print(self, *values, **kwargs):
            output.append(" ".join(str(value) for value in values))

    monkeypatch.setattr(cli, "console", RecordingConsole())

    cli.db_upgrade()

    backup_path = tmp_path / "profile_backup_v1_to_v2_2026-08-27-0000.back"
    assert events == ["backup", "init"]
    assert backup_path.read_bytes() == original_bytes
    assert db_path.read_bytes() == b"database changed by init"
    rendered_output = "\n".join(output)
    assert f"Path: {backup_path}" in rendered_output
    assert "Size on disk: 25 bytes" in rendered_output
    assert str(previous_backup) in rendered_output
    assert "10 bytes" in rendered_output
    assert "deleted when no longer needed to save disk space" in rendered_output


def test_list_migration_backups_only_returns_backups_for_database(tmp_path):
    db_path = tmp_path / "profile_with_underscores.db"
    matching = [
        tmp_path / "profile_with_underscores_1_2_20260826T000000000000Z.back",
        tmp_path / "profile_with_underscores_backup_v2_to_v3_2026-08-27-0000.back",
        tmp_path / "profile_with_underscores_backup_v2_to_v3_2026-08-27-0000-1.back",
    ]
    for backup in matching:
        backup.write_bytes(b"backup")
    (tmp_path / "profile_with_underscores_notes.back").write_bytes(b"not a backup")
    (tmp_path / "other_1_2_20260827T000000000000Z.back").write_bytes(b"other db")

    assert migrations.list_migration_backups(db_path) == matching
