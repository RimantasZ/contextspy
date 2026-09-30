# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from contextspy.db.models import Base
from contextspy.db.maintenance_lock import acquire_database_lock, release_database_lock

_engine = None
_SessionLocal = None
_db_lock_handle = None
_db_lock_path: Path | None = None
_SQLITE_BUSY_TIMEOUT_MS = 250
logger = logging.getLogger(__name__)


def init_db(db_path: Path) -> None:
    global _engine, _SessionLocal, _db_lock_handle, _db_lock_path
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = db_path.resolve()
    reuse_lock = _db_lock_handle is not None and _db_lock_path == resolved_path
    new_lock = None if reuse_lock or str(db_path) == ":memory:" else acquire_database_lock(db_path)
    try:
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={
                "check_same_thread": False,
                "timeout": _SQLITE_BUSY_TIMEOUT_MS / 1000,
            },
            echo=False,
        )
    except Exception:
        if new_lock is not None:
            release_database_lock(new_lock)
        raise

    @event.listens_for(engine, "connect")
    def _set_busy_timeout(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    try:
        if str(db_path) != ":memory:":
            try:
                with engine.connect() as conn:
                    mode = conn.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            except OperationalError as exc:
                raise RuntimeError(
                    f"Could not enable SQLite WAL for {db_path}. Stop other ContextSpy "
                    "processes and check database permissions before restarting."
                ) from exc
            if str(mode).lower() != "wal":
                raise RuntimeError(
                    f"SQLite did not enable WAL for {db_path} (returned {mode!r}). "
                    "Check the database filesystem and permissions."
                )
            logger.info("SQLite journal mode: WAL")
        Base.metadata.create_all(engine)
        _migrate(engine)
    except Exception:
        engine.dispose()
        if new_lock is not None:
            release_database_lock(new_lock)
        raise

    previous_engine = _engine
    previous_lock = _db_lock_handle
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    _db_lock_handle = previous_lock if reuse_lock else new_lock
    _db_lock_path = resolved_path if _db_lock_handle is not None else None
    if previous_engine is not None:
        previous_engine.dispose()
    if previous_lock is not None and not reuse_lock:
        release_database_lock(previous_lock)


def _migrate(engine) -> None:
    """Apply additive schema migrations for existing databases."""
    new_columns = [
        ("sessions", "next_request_seq", "INTEGER NOT NULL DEFAULT 1"),
        ("requests", "started_at", "DATETIME"),
        ("requests", "cache_read_tokens", "INTEGER"),
        ("requests", "cache_creation_tokens", "INTEGER"),
        ("requests", "ttft_ms", "INTEGER"),
        ("requests", "tokens_output_text", "INTEGER NOT NULL DEFAULT 0"),
        ("requests", "tokens_output_thinking", "INTEGER NOT NULL DEFAULT 0"),
        ("requests", "provider_reasoning_tokens", "INTEGER"),
        ("requests", "usage_extra", "TEXT"),
        ("requests", "session_seq", "INTEGER"),
        ("requests", "transport", "TEXT NOT NULL DEFAULT 'http'"),
        ("requests", "response_transport", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("requests", "response_reconstructed", "INTEGER NOT NULL DEFAULT 0"),
        ("requests", "response_complete", "INTEGER NOT NULL DEFAULT 0"),
        ("requests", "capture_error", "TEXT"),
        ("requests", "response_events", "TEXT"),
        ("requests", "canonical_request_body", "TEXT"),
        ("requests", "canonical_response_body", "TEXT"),
        ("requests", "provider_response_id", "TEXT"),
        ("requests", "predecessor_response_id", "TEXT"),
        ("requests", "invocation_outcome", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("requests", "context_fidelity", "TEXT NOT NULL DEFAULT 'complete'"),
        ("requests", "context_notes", "TEXT"),
        ("requests", "stream_hint_source", "TEXT"),
        ("requests", "stream_hint_digest", "TEXT"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                # Column already exists — ignore
                pass
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_requests_provider_response "
            "ON requests (provider, provider_response_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_requests_predecessor_response "
            "ON requests (predecessor_response_id)"
        ))
        # Kept out of SQLAlchemy metadata so an old database containing legacy
        # duplicate ordinals can still start and run its explicit v5 repair.
        # NULL session/sequence values remain allowed by SQLite.
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_session_seq_unique "
                "ON requests (session_id, session_seq)"
            ))
        except Exception:
            pass
        conn.commit()


def get_engine():
    return _engine


def dispose_engine() -> None:
    global _engine, _SessionLocal, _db_lock_handle, _db_lock_path
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    if _db_lock_handle is not None:
        release_database_lock(_db_lock_handle)
    _db_lock_handle = None
    _db_lock_path = None


@contextmanager
def get_db() -> Generator[OrmSession, None, None]:
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def startup_vacuum(settings=None) -> None:
    """Purge raw bodies and orphaned block contents past their retention window.

    Runs once, at server startup, using the [retention] settings from
    config.toml (default 7 days for both; 0 = keep forever). There is no
    background timer — a contextspy process left running for days will not
    re-purge until restarted (see docs/development.md).
    """
    if _engine is None:
        return
    if settings is None:
        from contextspy.config import Settings
        settings = Settings.load()

    with _engine.begin() as conn:
        raw_body_days = settings.retention.raw_body_days
        if raw_body_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=raw_body_days)
            conn.execute(
                text(
                    """
                    UPDATE requests
                    SET raw_request_body = NULL,
                        raw_response_body = NULL,
                        canonical_request_body = NULL,
                        canonical_response_body = NULL,
                        response_events = NULL
                    WHERE timestamp < :cutoff
                      AND (raw_request_body IS NOT NULL
                           OR raw_response_body IS NOT NULL
                           OR canonical_request_body IS NOT NULL
                           OR canonical_response_body IS NOT NULL
                           OR response_events IS NOT NULL)
                    """
                ),
                {"cutoff": cutoff.isoformat()},
            )

        block_content_days = settings.retention.block_content_days
        if block_content_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=block_content_days)
            # Keep any content still referenced by a block whose request is
            # newer than the cutoff (shared content across a session is only
            # GC'd once every request that uses it has aged out).
            conn.execute(
                text(
                    """
                    DELETE FROM block_contents
                    WHERE hash NOT IN (
                        SELECT DISTINCT b.content_hash
                        FROM blocks b
                        JOIN requests r ON r.id = b.request_id
                        WHERE b.content_hash IS NOT NULL AND r.timestamp >= :cutoff
                    )
                    """
                ),
                {"cutoff": cutoff.isoformat()},
            )
