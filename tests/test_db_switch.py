"""db-switch: swapping the active database with a shelved one."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner

from contextspy import cli
from contextspy.config import Settings
from contextspy.db import backups, database, migrations, switching
from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock


def _make_db(path, marker, *, wal=False):
    database.init_db(path)
    database.dispose_engine()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (marker,))
        conn.commit()
        if not wal:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
    return path


def _marker(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT value FROM marker").fetchone()[0]


def _names(path):
    return sorted(p.name for p in path.iterdir())


def test_name_validation():
    for bad in ("", "../x", "a/b", ".hidden", "-x", "a b", "x" * 65):
        with pytest.raises(switching.SwitchError):
            switching.validate_name(bad)
    assert switching.validate_name("test_1-a") == "test_1-a"


def test_new_then_switch_back_round_trips(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "original")
    version, _ = migrations.inspect_migration_state(db)
    backup = backups.create_backup(db, version)

    result = switching.switch_database(db, "scratch", create=True)
    assert (result.previous_name, result.new_name, result.created) == ("default", "scratch", True)
    assert not db.exists() and not backup.exists()
    assert (tmp_path / "db_switch" / "default" / "contextspy.db").is_file()
    assert (tmp_path / "db_switch" / "default" / backup.name).is_file()
    assert switching.active_name(db) == "scratch"

    # a fresh database is created by the next start, then used and shelved
    _make_db(db, "scratch-data")
    result = switching.switch_database(db, "default")
    assert result.previous_name == "scratch"
    assert _marker(db) == "original"
    assert backup.is_file()
    assert _marker(tmp_path / "db_switch" / "scratch" / "contextspy.db") == "scratch-data"
    assert not (tmp_path / "db_switch" / "default").exists()
    assert not [p for p in (tmp_path / "db_switch").iterdir() if p.name.startswith(".swap-")]
    assert switching.active_name(db) == "default"


def test_wal_is_checkpointed_and_sidecars_never_move(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "in-wal", wal=True)
    conn = sqlite3.connect(db)  # keeps -wal/-shm alive with committed, un-checkpointed data
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO marker VALUES ('later')")
    conn.commit()
    assert (tmp_path / "contextspy.db-wal").exists()
    conn.close()
    switching.switch_database(db, "other", create=True)
    assert not list(tmp_path.glob("contextspy.db-*"))
    shelved = tmp_path / "db_switch" / "default" / "contextspy.db"
    assert not list(shelved.parent.glob("*-wal")) and not list(shelved.parent.glob("*-shm"))
    with closing(sqlite3.connect(shelved)) as c:
        assert c.execute("SELECT count(*) FROM marker").fetchone()[0] == 2
    backups.inspect_backup(shelved)  # standalone


def test_never_overwrites_existing_slot_or_unrelated_files(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    (tmp_path / "config.toml").write_text("x")
    switching.switch_database(db, "b", create=True)
    _make_db(db, "b-data")
    # shelving the active 'b' under the taken name 'default' must be refused
    with pytest.raises(switching.SwitchError, match="--as"):
        switching.switch_database(db, "c", create=True, save_as="default")
    assert _marker(db) == "b-data"
    assert (tmp_path / "config.toml").read_text() == "x"
    assert _marker(tmp_path / "db_switch" / "default" / "contextspy.db") == "a"


def test_unknown_name_and_active_name_refused(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    with pytest.raises(switching.SwitchError, match="--new"):
        switching.switch_database(db, "typo")
    switching.switch_database(db, "b", create=True)
    with pytest.raises(switching.SwitchError, match="already the active"):
        switching.switch_database(db, "b")
    with pytest.raises(switching.SwitchError, match="already exists"):
        switching.switch_database(db, "default", create=True)
    with pytest.raises(switching.SwitchError, match="case"):
        switching.switch_database(db, "DEFAULT", create=True)


def test_refuses_while_lock_held(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    lock = acquire_database_lock(db)
    try:
        with pytest.raises(RuntimeError, match="in use"):
            switching.switch_database(db, "b", create=True)
    finally:
        release_database_lock(lock)
    assert db.exists()


def test_failure_rolls_back_every_move(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "contextspy.db", "a")
    version, _ = migrations.inspect_migration_state(db)
    backups.create_backup(db, version)
    switching.switch_database(db, "b", create=True)
    _make_db(db, "b-data")
    before = (_names(tmp_path), _names(tmp_path / "db_switch" / "default"))

    calls = {"n": 0}
    real_move = switching._move

    def flaky(src, dst, journal):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk went away")
        real_move(src, dst, journal)

    monkeypatch.setattr(switching, "_move", flaky)
    with pytest.raises(OSError):
        switching.switch_database(db, "default")
    monkeypatch.setattr(switching, "_move", real_move)
    assert switching.active_name(db) == "b"
    assert _marker(db) == "b-data"
    after = (_names(tmp_path), _names(tmp_path / "db_switch" / "default"))
    assert after[1] == before[1]
    assert [n for n in after[0] if n != "db_switch"] == [n for n in before[0] if n != "db_switch"]
    assert not [p for p in (tmp_path / "db_switch").iterdir() if p.name.startswith(".swap-")]


def test_leftover_staging_blocks_switch(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    (tmp_path / "db_switch" / ".swap-crashed").mkdir(parents=True)
    with pytest.raises(switching.SwitchError, match="did not finish"):
        switching.switch_database(db, "b", create=True)


def test_incoming_with_sidecar_is_rejected(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    switching.switch_database(db, "b", create=True)
    (tmp_path / "db_switch" / "default" / "contextspy.db-wal").write_bytes(b"x")
    with pytest.raises(switching.SwitchError, match="sidecar"):
        switching.switch_database(db, "default")
    assert not db.exists()


def test_list_databases(tmp_path):
    db = _make_db(tmp_path / "contextspy.db", "a")
    switching.switch_database(db, "b", create=True)
    _make_db(db, "b-data")
    entries = switching.list_databases(db)
    assert [(e.name, e.active) for e in entries] == [("b", True), ("default", False)]


def test_cli_switch_and_list(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "contextspy.db", "a")
    settings = Settings()
    settings.storage.db_path = db
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda s: False)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["db-switch", "scratch", "--new"])
    assert result.exit_code == 0, result.output
    assert "scratch" in result.output
    result = runner.invoke(cli.app, ["db-switch", "--list"])
    assert result.exit_code == 0 and "default" in result.output
    result = runner.invoke(cli.app, ["db-switch", "nope"])
    assert result.exit_code == 1
    monkeypatch.setattr(cli, "_configured_backend_reachable", lambda s: True)
    result = runner.invoke(cli.app, ["db-switch", "default"])
    assert result.exit_code == 1 and "Stop ContextSpy" in result.output
