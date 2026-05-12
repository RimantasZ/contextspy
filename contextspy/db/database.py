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


def startup_vacuum() -> None:
    """NULL out raw bodies for any request older than 7 days."""
    if _engine is None:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with _engine.begin() as conn:
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
