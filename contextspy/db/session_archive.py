# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Explicit, one-way session archive: drop raw payloads and block text, keep the analysis.

Archiving removes what is large and re-derivable only from the original traffic (the five request/response
body columns and the ``block_contents`` text no other session needs) and keeps everything the analysis views
use: block rows (hash, type, category, tokens, source key, JSON path), lineage and conversation data.
See plans/archive/session-archive.md.

Concurrency: SQLite serialises writers, and ContextSpy's capture path keeps writing while an archive runs. Every
write here is therefore a short transaction. The content cleanup is the delicate part: it must never delete content
a capture in another session has just started to reference. Each chunk takes the write lock first
(``_take_write_lock``), then measures and deletes with the "not referenced outside this session" condition inside the
DELETE itself, so a capture either committed its block row before the delete (and is seen) or runs after it (and
re-inserts the content).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from contextspy.db.models import Session

logger = logging.getLogger(__name__)

HASH_CHUNK = 500                 # below SQLite's bound-variable limit, short write transactions
VACUUM_CHUNK_PAGES = 1000        # ~4 MB per incremental_vacuum step
VACUUM_BUDGET_SECONDS = 30.0

_BODY_COLUMNS = (
    "raw_request_body", "raw_response_body", "canonical_request_body",
    "canonical_response_body", "response_events",
)
_ANY_BODY = " OR ".join(f"{column} IS NOT NULL" for column in _BODY_COLUMNS)
_BODY_BYTES = " + ".join(f"COALESCE(LENGTH(CAST({column} AS BLOB)), 0)" for column in _BODY_COLUMNS)

_in_progress: set[str] = set()
_in_progress_lock = Lock()


class SessionNotFound(KeyError):
    pass


class SessionStillActive(ValueError):
    pass


class ArchiveInProgress(RuntimeError):
    pass


def _take_write_lock(db: OrmSession, session_id: str) -> None:
    """A harmless write that acquires SQLite's write lock before the transaction reads anything."""
    db.execute(text("UPDATE sessions SET name = name WHERE id = :sid"), {"sid": session_id})


def _chunks(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _hash_params(chunk: list[str]) -> tuple[str, dict]:
    placeholders = ", ".join(f":h{index}" for index in range(len(chunk)))
    return placeholders, {f"h{index}": value for index, value in enumerate(chunk)}


def archive_session_data(db: OrmSession, session_id: str) -> dict:
    """Remove the session's payloads and unreferenced block text, then mark it archived.

    Raises ``SessionNotFound``, ``SessionStillActive`` (an active session cannot be archived) or
    ``ArchiveInProgress``. Safe to repeat: an archived session is purged again (catching stragglers).
    Returns ``{"session": Session, "freed": {...}, "already_archived": bool}``; commits as it goes.
    """
    session = db.get(Session, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    if session.is_active:
        raise SessionStillActive(session_id)
    with _in_progress_lock:
        if session_id in _in_progress:
            raise ArchiveInProgress(session_id)
        _in_progress.add(session_id)
    try:
        already_archived = session.archived_at is not None
        db.commit()  # end the read snapshot: nothing below may start from a stale one

        request_rows, request_bytes = db.execute(
            text(
                f"SELECT COUNT(*), COALESCE(SUM({_BODY_BYTES}), 0) FROM requests "
                f"WHERE session_id = :sid AND ({_ANY_BODY})"
            ),
            {"sid": session_id},
        ).one()
        if request_rows:
            db.execute(
                text(
                    f"UPDATE requests SET {', '.join(f'{c} = NULL' for c in _BODY_COLUMNS)} "
                    f"WHERE session_id = :sid AND ({_ANY_BODY})"
                ),
                {"sid": session_id},
            )
            db.commit()

        candidates = [
            row[0] for row in db.execute(
                text(
                    "SELECT DISTINCT b.content_hash FROM blocks b JOIN requests r ON r.id = b.request_id "
                    "WHERE r.session_id = :sid AND b.content_hash IS NOT NULL"
                ),
                {"sid": session_id},
            )
        ]
        db.commit()
        content_rows = content_bytes = 0
        for chunk in _chunks(candidates, HASH_CHUNK):
            placeholders, params = _hash_params(chunk)
            _take_write_lock(db, session_id)
            unreferenced = (
                f"hash IN ({placeholders}) AND hash NOT IN ("
                f"SELECT b.content_hash FROM blocks b JOIN requests r ON r.id = b.request_id "
                f"LEFT JOIN sessions s ON s.id = r.session_id "
                f"WHERE b.content_hash IN ({placeholders}) "
                # Only content a still-retained request can show keeps a hash alive: requests without a session and
                # requests of other sessions that are not archived (an archived session has no content to protect).
                f"AND (r.session_id IS NULL OR (r.session_id != :sid AND s.archived_at IS NULL)))"
            )
            rows, size = db.execute(
                text(f"SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(content AS BLOB))), 0) FROM block_contents WHERE {unreferenced}"),
                {**params, "sid": session_id},
            ).one()
            if rows:
                db.execute(text(f"DELETE FROM block_contents WHERE {unreferenced}"), {**params, "sid": session_id})
            db.commit()
            content_rows += rows
            content_bytes += size

        if not already_archived:
            session = db.get(Session, session_id)
            session.archived_at = datetime.now(timezone.utc)
            db.commit()
        else:
            session = db.get(Session, session_id)
        return {
            "session": session,
            "already_archived": already_archived,
            "freed": {
                "requests": request_rows,
                "request_body_bytes": request_bytes,
                "content_rows": content_rows,
                "content_bytes": content_bytes,
            },
        }
    finally:
        with _in_progress_lock:
            _in_progress.discard(session_id)


def reclaim_space(engine, *, budget_seconds: float = VACUUM_BUDGET_SECONDS, chunk_pages: int = VACUUM_CHUNK_PAGES) -> dict:
    """Return freed pages to the filesystem when the database is in incremental auto-vacuum mode.

    Uses the raw DBAPI connection: through SQLAlchemy ``PRAGMA incremental_vacuum`` closes its result after a
    single step and frees about one page per call. Each step is its own short write transaction, so capture is not
    held up; the loop stops at the time budget and leaves the rest for the next run.
    """
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        mode = cursor.execute("PRAGMA auto_vacuum").fetchone()[0]
        page_size = cursor.execute("PRAGMA page_size").fetchone()[0]
        free_before = cursor.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        raw.close()
    if mode != 2:
        return {
            "auto_vacuum": "none",
            "reclaimed_bytes": 0,
            "free_bytes_remaining": free_before * page_size,
            "note": (
                "The database file does not shrink by itself. Stop ContextSpy and run `contextspy db-compact` "
                "to give the freed space back to the disk and enable online shrinking."
            ),
        }

    deadline = time.monotonic() + budget_seconds
    free = free_before
    while free > 0 and time.monotonic() < deadline:
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute(f"PRAGMA incremental_vacuum({int(chunk_pages)})")
            cursor.fetchall()  # the statement only does work as its rows are fetched
            raw.commit()
            free = cursor.execute("PRAGMA freelist_count").fetchone()[0]
        finally:
            raw.close()
    raw = engine.raw_connection()
    try:
        raw.cursor().execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
    except Exception:  # the file is already smaller; a busy checkpoint is not an error
        logger.debug("checkpoint after archive was not possible", exc_info=True)
    finally:
        raw.close()
    return {
        "auto_vacuum": "incremental",
        "reclaimed_bytes": (free_before - free) * page_size,
        "free_bytes_remaining": free * page_size,
        "note": None if free == 0 else "Stopped at the time limit; the rest is returned by the next archive or `db-compact`.",
    }
