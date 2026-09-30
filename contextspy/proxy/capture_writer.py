"""Atomic, retryable persistence for completed proxy exchanges.

The envelope is prepared once by the capture pipeline. Each retry uses a new
SQLAlchemy session so a failed commit never reuses a rolled-back transaction or
allocates a permanent session sequence number.
"""
from __future__ import annotations

import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.exc import OperationalError

from contextspy.analysis.blocks import Block
from contextspy.db import crud
from contextspy.db.database import get_db
from contextspy.db.models import Request

logger = logging.getLogger(__name__)

_RETRY_BUDGET_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 0.2


@dataclass(frozen=True)
class CaptureEnvelope:
    request_id: str
    provider: str
    provider_response_id: str | None
    transport: str
    data: Mapping[str, Any]
    blocks: tuple[Block, ...]
    tool_rows: tuple[Mapping[str, Any], ...]


def _is_transient_sqlite_lock(exc: OperationalError) -> bool:
    original = exc.orig
    if not isinstance(original, sqlite3.OperationalError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    # Older Python/SQLite combinations may not expose sqlite_errorcode.
    return str(original).lower() in {
        "database is locked",
        "database table is locked",
        "database schema is locked",
    }


def _persist_once(envelope: CaptureEnvelope) -> dict | None:
    with get_db() as db:
        # A commit can succeed while its acknowledgement fails. The stable UUID
        # lets the next attempt discover that result without inserting twice.
        existing = db.get(Request, envelope.request_id)
        if existing is not None:
            return existing.to_dict(include_raw=False)
        if envelope.provider_response_id:
            duplicate = crud.get_request_by_provider_response_id(
                db, envelope.provider, envelope.provider_response_id,
            )
            if duplicate is not None:
                logger.debug(
                    "Skipping duplicate provider response %s",
                    envelope.provider_response_id,
                )
                return None

        record = crud.create_request(db, dict(envelope.data))
        if envelope.blocks:
            crud.insert_blocks(db, record.id, list(envelope.blocks))
        if envelope.tool_rows:
            crud.upsert_tool_stats(db, record.id, list(envelope.tool_rows))
        # Serialize while attached, but the caller must not broadcast until
        # get_db() exits and the commit is confirmed.
        return record.to_dict(include_raw=False)


def persist_capture(envelope: CaptureEnvelope) -> dict | None:
    deadline = time.monotonic() + _RETRY_BUDGET_SECONDS
    attempts = 0
    while True:
        attempts += 1
        try:
            return _persist_once(envelope)
        except OperationalError as exc:
            if not _is_transient_sqlite_lock(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "capture_not_saved request_id=%s transport=%s attempts=%d reason=sqlite_busy",
                    envelope.request_id, envelope.transport, attempts,
                )
                raise
            backoff = min(_BACKOFF_CAP_SECONDS, 0.025 * (2 ** min(attempts - 1, 3)))
            time.sleep(min(remaining, backoff * random.uniform(0.75, 1.25)))
