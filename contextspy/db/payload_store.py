# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Content-addressed storage for large request bodies.

Agents resend (or ContextSpy expands, for ``thread.continue`` and ``previous_response_id``) the whole
conversation on every request, so consecutive request bodies repeat almost every byte. Storing each body
whole makes the database grow with the square of the conversation length.

A large body is therefore stored *packed*: every top-level JSON value (each element, for an array such as
``messages``, ``input``, ``tools`` or ``system``) of at least ``MIN_PART_CHARS`` is cut out into
``payload_parts``, keyed by its hash, and the request row keeps a small layout:

    {"v": 1, "t": [text before part 0, between part 0 and 1, ..., after the last part], "p": [hash, ...]}

The parts are exact slices of the original text, so ``"".join`` of the pieces gives the stored body back
byte for byte, whatever its formatting. A body that is not a JSON object, or is below ``PACK_MIN_CHARS``,
stays inline in its column.

Read bodies through ``request_bodies`` (or ``unpack``): a packed row has ``NULL`` in the body column and its
layout in ``<column>_parts``. Unreferenced parts are removed by ``delete_unreferenced`` wherever requests or
their bodies are removed (archive, retention purge, session delete).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as OrmSession

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
# Bodies below this size stay inline: packing them saves little.
PACK_MIN_CHARS = 32 * 1024
# Smaller values stay in the layout's text pieces; a part reference costs about 40 characters.
MIN_PART_CHARS = 128
_HASH_CHARS = 32
_QUERY_CHUNK = 500  # below SQLite's bound-variable limit

# Request body columns that may be packed, each with the column holding its layout.
PACKED_COLUMNS = {
    "raw_request_body": "raw_request_parts",
    "canonical_request_body": "canonical_request_parts",
}

_WHITESPACE = re.compile(r"[ \t\n\r]*")
_decoder = json.JSONDecoder()


def _part_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()[:_HASH_CHARS]


def _skip(text_: str, index: int) -> int:
    return _WHITESPACE.match(text_, index).end()


def _value_spans(body: str) -> list[tuple[int, int]] | None:
    """Spans of the top-level member values of a JSON object (array members: of each element).

    Returns ``None`` when ``body`` is not a single JSON object.
    """
    spans: list[tuple[int, int]] = []
    try:
        i = _skip(body, 0)
        if body[i] != "{":
            return None
        i = _skip(body, i + 1)
        if body[i] == "}":
            return spans if not body[_skip(body, i + 1):] else None
        while True:
            if body[i] != '"':
                return None
            _key, i = json.decoder.scanstring(body, i + 1)
            i = _skip(body, i)
            if body[i] != ":":
                return None
            i = _skip(body, i + 1)
            if body[i] == "[":
                j = _skip(body, i + 1)
                if body[j] == "]":
                    i = j + 1
                else:
                    while True:
                        _value, end = _decoder.raw_decode(body, j)
                        spans.append((j, end))
                        j = _skip(body, end)
                        if body[j] == ",":
                            j = _skip(body, j + 1)
                            continue
                        if body[j] != "]":
                            return None
                        i = j + 1
                        break
            else:
                _value, end = _decoder.raw_decode(body, i)
                spans.append((i, end))
                i = end
            i = _skip(body, i)
            if body[i] == ",":
                i = _skip(body, i + 1)
                continue
            if body[i] != "}":
                return None
            return spans if _skip(body, i + 1) == len(body) else None
    except (IndexError, ValueError):
        return None


def pack(body: str) -> tuple[dict, dict[str, str]] | None:
    """Split ``body`` into a layout and its parts, or ``None`` if it should stay inline."""
    if len(body) < PACK_MIN_CHARS:
        return None
    spans = _value_spans(body)
    if not spans:
        return None
    pieces: list[str] = []
    hashes: list[str] = []
    parts: dict[str, str] = {}
    cursor = 0
    for start, end in spans:
        if end - start < MIN_PART_CHARS:
            continue
        content = body[start:end]
        digest = _part_hash(content)
        pieces.append(body[cursor:start])
        hashes.append(digest)
        parts[digest] = content
        cursor = end
    if not hashes:
        return None
    pieces.append(body[cursor:])
    return {"v": LAYOUT_VERSION, "t": pieces, "p": hashes}, parts


def write_parts(db: OrmSession, parts: dict[str, str]) -> None:
    """Insert parts that are not stored yet, in the caller's transaction.

    INSERT OR IGNORE (never "read, then insert what is missing"): once this transaction holds the write
    lock, a concurrent cleanup cannot delete a part this row is about to reference (see
    ``session_archive``).
    """
    if parts:
        from contextspy.db.models import PayloadPart

        db.execute(
            sqlite_insert(PayloadPart).on_conflict_do_nothing(index_elements=["hash"]),
            [{"hash": digest, "content": content} for digest, content in parts.items()],
        )


def pack_fields(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``data`` (Request column values) with its large request bodies replaced by layouts, plus the parts
    those layouts reference. Pure: the caller writes the parts (``write_parts``) in the row's transaction."""
    packed = dict(data)
    all_parts: dict[str, str] = {}
    for column, layout_column in PACKED_COLUMNS.items():
        body = packed.get(column)
        if not isinstance(body, str):
            continue
        result = pack(body)
        if result is None:
            continue
        layout, parts = result
        all_parts.update(parts)
        packed[column] = None
        packed[layout_column] = json.dumps(layout, ensure_ascii=False, separators=(",", ":"))
    return packed, all_parts


def _fetch_parts(db: OrmSession, hashes: Iterable[str]) -> dict[str, str]:
    wanted = list(dict.fromkeys(hashes))
    found: dict[str, str] = {}
    for start in range(0, len(wanted), _QUERY_CHUNK):
        chunk = wanted[start:start + _QUERY_CHUNK]
        params = {f"h{i}": value for i, value in enumerate(chunk)}
        placeholders = ", ".join(f":h{i}" for i in range(len(chunk)))
        for digest, content in db.execute(
            text(f"SELECT hash, content FROM payload_parts WHERE hash IN ({placeholders})"), params,
        ):
            found[digest] = content
    return found


def unpack(db: OrmSession, layout_text: str | None) -> str | None:
    """The body a layout describes, or ``None`` (and an error log) if a part is missing."""
    if layout_text is None:
        return None
    layout = json.loads(layout_text)
    if layout.get("v") != LAYOUT_VERSION:
        logger.error("Unknown payload layout version %r", layout.get("v"))
        return None
    pieces, hashes = layout["t"], layout["p"]
    parts = _fetch_parts(db, hashes)
    missing = [digest for digest in hashes if digest not in parts]
    if missing:
        logger.error("Payload parts missing from the database (%d of %d)", len(missing), len(hashes))
        return None
    out = [pieces[0]]
    for digest, piece in zip(hashes, pieces[1:]):
        out.append(parts[digest])
        out.append(piece)
    return "".join(out)


def body(db: OrmSession, row, column: str) -> str | None:
    """One request body column of ``row``, unpacked if it is stored packed."""
    value = getattr(row, column)
    if value is not None:
        return value
    layout_column = PACKED_COLUMNS.get(column)
    return unpack(db, getattr(row, layout_column)) if layout_column else None


def request_bodies(db: OrmSession, row) -> tuple[str | None, str | None]:
    """``(raw_request_body, canonical_request_body)`` of a Request row, unpacked."""
    return body(db, row, "raw_request_body"), body(db, row, "canonical_request_body")


def _referenced_hashes_sql() -> str:
    return " UNION ".join(
        f"SELECT j.value FROM requests r, json_each(r.{layout}, '$.p') j WHERE r.{layout} IS NOT NULL"
        for layout in PACKED_COLUMNS.values()
    )


def hashes_of(db: OrmSession, where: str, params: dict) -> list[str]:
    """Distinct part hashes referenced by the requests matching ``where`` (SQL on alias ``r``)."""
    sql = " UNION ".join(
        f"SELECT j.value FROM requests r, json_each(r.{layout}, '$.p') j "
        f"WHERE r.{layout} IS NOT NULL AND ({where})"
        for layout in PACKED_COLUMNS.values()
    )
    return [row[0] for row in db.execute(text(sql), params)]


def unreferenced_condition(placeholders: str | None = None) -> str:
    """SQL condition on ``payload_parts`` for parts no request references (optionally among a hash list)."""
    condition = f"hash NOT IN ({_referenced_hashes_sql()})"
    return f"hash IN ({placeholders}) AND {condition}" if placeholders else condition


def delete_unreferenced(conn_or_db) -> int:
    """Delete every part no request references; returns the number of rows removed."""
    result = conn_or_db.execute(text(f"DELETE FROM payload_parts WHERE {unreferenced_condition()}"))
    return result.rowcount or 0
