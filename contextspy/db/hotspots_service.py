# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Hot spots: which blocks, sources or files add up to the most visible tokens in a scope.

One pass over the scope's input blocks fills a TEMP table of per-group aggregates (``aggregate_select``);
rows, totals, summary, the in-context filter, sorting and paging are all derived from that table, and only
the returned rows (at most ``MAX_LIMIT``) cost a second, bounded lookup for their descriptive columns.

**Query shape is a requirement, not a style choice.** The scope table is always the outer loop
(``FROM hs_scope s CROSS JOIN blocks b ON b.request_id = s.id``): left to itself SQLite scans
``idx_blocks_content_hash`` for a ``GROUP BY content_hash`` and is 15-100x slower. A test asserts the plan.
Block identity is the content hash; input blocks only (output blocks reappear as input in the next request,
so counting both would double count). See plans/archive/hot-spots.md.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.activity import activity_for
from contextspy.analysis.block_hotspots import (
    POSITION_SHIFT, block_label, count_runs, preview_text, share_pct, unpack_latest,
)
from contextspy.db.block_occurrence_service import SessionScope, scope_for_session
from contextspy.db.models import Session

GROUPS = ("block", "source", "file")
SORTS = ("total_tokens", "occurrences")
IN_CONTEXT = ("all", "current", "dropped")
MAX_LIMIT = 100
MAX_OFFSET = 1000

_GROUP_KEY = {
    "block": "b.content_hash",
    "source": "COALESCE(b.source_key, 'unknown')",
    "file": "b.file_path",
}
_GROUP_EXTRAS = {
    "block": (
        "MIN(b.token_count) AS tmin, MAX(b.token_count) AS tmax, GROUP_CONCAT(DISTINCT b.block_type) AS types, "
        # Scope positions of every occurrence, for the run count: cheaper than a second lookup by hash,
        # which has to walk the hash index across all sessions.
        "GROUP_CONCAT(s.pos) AS poss"
    ),
    "source": (
        f"COUNT(DISTINCT b.content_hash) AS versions, MAX(b.token_count * {POSITION_SHIFT} + b.id) AS largest_key"
    ),
    "file": (
        "COUNT(DISTINCT b.content_hash) AS versions, "
        "SUM(CASE WHEN b.block_type = 'tool_result' THEN b.token_count ELSE 0 END) AS result_tokens, "
        "SUM(CASE WHEN b.block_type = 'tool_call' THEN b.token_count ELSE 0 END) AS call_tokens"
    ),
}


def aggregate_select(group: str, *, category: str | None = None, block_type: str | None = None,
                     source: str | None = None) -> tuple[str, dict[str, Any]]:
    """The single grouping statement (SQL, parameters). It does not depend on sort, paging or in-context."""
    conditions = ["b.direction = 'input'"]
    params: dict[str, Any] = {}
    if category:
        conditions.append("b.category = :category")
        params["category"] = category
    if block_type:
        conditions.append("b.block_type = :block_type")
        params["block_type"] = block_type
    if source:
        conditions.append("COALESCE(b.source_key, 'unknown') = :source")
        params["source"] = source
    sql = (
        f"SELECT {_GROUP_KEY[group]} AS k, COUNT(*) AS occ, COUNT(DISTINCT b.request_id) AS reqs, "
        f"SUM(b.token_count) AS total, MIN(s.pos) AS first_pos, "
        f"MAX(s.pos * {POSITION_SHIFT} + b.id) AS latest_key, {_GROUP_EXTRAS[group]} "
        "FROM hs_scope s CROSS JOIN blocks b ON b.request_id = s.id "
        f"WHERE {' AND '.join(conditions)} GROUP BY 1"
    )
    return sql, params


def _load_scope(db: OrmSession, scope: SessionScope) -> None:
    db.execute(text("DROP TABLE IF EXISTS temp.hs_scope"))
    db.execute(text("DROP TABLE IF EXISTS temp.hs_agg"))
    db.execute(text("CREATE TEMP TABLE hs_scope (id TEXT PRIMARY KEY, pos INTEGER NOT NULL) WITHOUT ROWID"))
    rows = [{"id": request.request_id, "pos": position} for position, request in enumerate(scope.requests)]
    for start in range(0, len(rows), 500):
        db.execute(text("INSERT INTO hs_scope (id, pos) VALUES (:id, :pos)"), rows[start:start + 500])


def _drop_temp(db: OrmSession) -> None:
    db.execute(text("DROP TABLE IF EXISTS temp.hs_agg"))
    db.execute(text("DROP TABLE IF EXISTS temp.hs_scope"))


def _in_clause(prefix: str, values: list) -> tuple[str, dict[str, Any]]:
    names = [f":{prefix}{index}" for index in range(len(values))]
    return ", ".join(names), {f"{prefix}{index}": value for index, value in enumerate(values)}


def _describe(db: OrmSession, block_ids: list[int]) -> dict[int, dict]:
    """Descriptive columns (and a content preview when the text is still stored) for specific block rows."""
    if not block_ids:
        return {}
    names, params = _in_clause("i", block_ids)
    rows = db.execute(text(
        "SELECT b.id, b.request_id, b.content_hash, b.block_type, b.category, b.tool_name, b.source_key, "
        "b.file_path, b.attrs, b.token_count, substr(c.content, 1, 400) AS preview, c.hash IS NOT NULL AS has_content "
        f"FROM blocks b LEFT JOIN block_contents c ON c.hash = b.content_hash WHERE b.id IN ({names})"
    ), params).mappings().all()
    described: dict[int, dict] = {}
    for row in rows:
        try:
            attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        except json.JSONDecodeError:
            attrs = {}
        item_type = attrs.get("provider_item_type")
        described[row["id"]] = {
            "request_id": row["request_id"],
            "block_type": row["block_type"],
            "category": row["category"],
            "tool_name": row["tool_name"],
            "source_key": row["source_key"],
            "activity": activity_for(row["source_key"]),
            "file_path": row["file_path"],
            "label": block_label(
                row["block_type"], tool_name=row["tool_name"], source_key=row["source_key"],
                file_path=row["file_path"], provider_item_type=item_type if isinstance(item_type, str) else None,
            ),
            "preview": preview_text(row["preview"]) if row["has_content"] else None,
            "content_purged": bool(row["content_hash"]) and not row["has_content"],
            "token_count": row["token_count"],
        }
    return described


def _seq(scope: SessionScope, position: int) -> int | None:
    return scope.requests[position].session_seq if 0 <= position < len(scope.requests) else None


def _occurrence(scope: SessionScope, described: dict[int, dict], position: int, block_id: int) -> dict:
    return {
        "request_id": scope.requests[position].request_id if 0 <= position < len(scope.requests) else None,
        "block_id": block_id,
        "session_seq": _seq(scope, position),
    }


def get_session_hotspots(
    db: OrmSession, session_id: str, *, group: str = "block", scope: str = "conversation",
    conversation: str | None = None, sort: str = "total_tokens", category: str | None = None,
    block_type: str | None = None, source: str | None = None, in_context: str = "all",
    limit: int = 25, offset: int = 0,
) -> dict | None:
    """The ranking described in plans/archive/hot-spots.md; None when the session does not exist."""
    if db.get(Session, session_id) is None:
        return None
    resolved = scope_for_session(
        db, session_id, scope, conversation_key=conversation, with_conversations=True,
    )
    last_position = len(resolved.requests) - 1
    fidelity: dict[str, int] = {}
    for request in resolved.requests:
        fidelity[request.context_fidelity] = fidelity.get(request.context_fidelity, 0) + 1

    try:
        _load_scope(db, resolved)
        select_sql, params = aggregate_select(group, category=category, block_type=block_type, source=source)
        db.execute(text(f"CREATE TEMP TABLE hs_agg AS {select_sql}"), params)

        totals = db.execute(text("SELECT COALESCE(SUM(total), 0) AS tokens, COALESCE(SUM(occ), 0) AS occurrences FROM hs_agg")).one()
        total, occurrences_total = totals.tokens or 0, totals.occurrences or 0
        unidentifiable = None
        if group == "block":
            row = db.execute(text("SELECT occ, total FROM hs_agg WHERE k IS NULL")).first()
            unidentifiable = {"blocks": row.occ if row else 0, "tokens": row.total if row else 0}

        where = ["k IS NOT NULL"]
        query_params: dict[str, Any] = {}
        if in_context != "all":
            where.append(f"latest_key / {POSITION_SHIFT} {'=' if in_context == 'current' else '<'} :last")
            query_params["last"] = last_position
        condition = " AND ".join(where)
        total_rows = db.execute(text(f"SELECT COUNT(*) FROM hs_agg WHERE {condition}"), query_params).scalar() or 0
        order = "total DESC, occ DESC, k" if sort == "total_tokens" else "occ DESC, total DESC, k"
        page = db.execute(text(
            f"SELECT * FROM hs_agg WHERE {condition} ORDER BY {order} LIMIT :limit OFFSET :offset"
        ), {**query_params, "limit": limit, "offset": offset}).mappings().all()

        pointers = [
            unpack_latest(r["latest_key"])[1] if group != "source" else unpack_latest(r["largest_key"])[1]
            for r in page
        ]
        described = _describe(db, pointers)
    finally:
        _drop_temp(db)

    rows: list[dict] = []
    for record, pointer in zip(page, pointers):
        last_pos, block_id = unpack_latest(record["latest_key"])
        latest = _occurrence(resolved, described, last_pos, block_id)
        common = {
            "key": record["k"],
            "occurrence_count": record["occ"],
            "request_count": record["reqs"],
            "total_tokens": record["total"],
            "share_pct": share_pct(record["total"], total),
        }
        info = described.get(pointer, {})
        if group == "block":
            block_types = sorted(set((record["types"] or "").split(","))) if record["types"] else []
            rows.append({
                **common,
                "block_type": info.get("block_type"),
                **({"block_types": block_types} if len(block_types) > 1 else {}),
                "category": info.get("category"),
                "tool_name": info.get("tool_name"),
                "source_key": info.get("source_key"),
                "activity": info.get("activity"),
                "file_path": info.get("file_path"),
                "label": info.get("label"),
                "preview": info.get("preview"),
                "content_purged": info.get("content_purged", False),
                "tokens_per_occurrence": record["tmin"] if record["tmin"] == record["tmax"] else None,
                "first_seen_session_seq": _seq(resolved, record["first_pos"]),
                "last_seen_session_seq": _seq(resolved, last_pos),
                "in_latest_request": last_pos == last_position,
                "run_count": count_runs(int(p) for p in record["poss"].split(",")),
                "latest": latest,
            })
        elif group == "source":
            largest_tokens, largest_id = unpack_latest(record["largest_key"])
            largest = {
                "request_id": info.get("request_id"), "block_id": largest_id,
                "session_seq": next(
                    (r.session_seq for r in resolved.requests if r.request_id == info.get("request_id")), None,
                ),
                "token_count": largest_tokens, "label": info.get("label"),
            }
            rows.append({
                **common, "source_key": record["k"], "activity": activity_for(record["k"]),
                "distinct_blocks": record["versions"], "largest": largest,
            })
        else:
            rows.append({
                **common, "file_path": record["k"], "distinct_versions": record["versions"],
                "result_tokens": record["result_tokens"], "call_tokens": record["call_tokens"],
                "first_seen_session_seq": _seq(resolved, record["first_pos"]),
                "last_seen_session_seq": _seq(resolved, last_pos),
                "in_latest_request": last_pos == last_position,
                "latest": latest,
            })

    returned_tokens = sum(row["total_tokens"] for row in rows)
    return {
        "scope": resolved.scope,
        "requested_scope": scope,
        "scope_note": resolved.note,
        "group": group,
        "sort": sort,
        "conversations": resolved.conversations,
        "summary": {
            "scope_request_count": len(resolved.requests),
            "visible_tokens_total": total,
            "occurrences_total": occurrences_total,
            "fidelity_counts": fidelity,
            "unidentifiable": unidentifiable,
            "returned_tokens": returned_tokens,
            "returned_share_pct": share_pct(returned_tokens, total),
        },
        "rows": rows,
        "total_rows": total_rows,
        "has_more": offset + len(rows) < total_rows,
    }
