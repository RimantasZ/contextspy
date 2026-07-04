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
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from contextspy.db.models import Base

_engine = None
_SessionLocal = None


def init_db(db_path: Path) -> None:
    global _engine, _SessionLocal
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(_engine)
    _migrate(_engine)


def _migrate(engine) -> None:
    """Apply additive schema migrations for existing databases."""
    new_columns = [
        ("cache_read_tokens", "INTEGER"),
        ("cache_creation_tokens", "INTEGER"),
        ("ttft_ms", "INTEGER"),
    ]
    with engine.connect() as conn:
        for col, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE requests ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                # Column already exists — ignore
                pass


def get_engine():
    return _engine


def dispose_engine() -> None:
    if _engine:
        _engine.dispose()


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
                    SET raw_request_body = NULL, raw_response_body = NULL
                    WHERE timestamp < :cutoff
                      AND (raw_request_body IS NOT NULL OR raw_response_body IS NOT NULL)
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
