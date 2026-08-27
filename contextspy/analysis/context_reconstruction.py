# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Provider-neutral best-effort context reconstruction."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from contextspy.analysis.blocks import (
    AnalyzedRequest,
    Block,
    BlockType,
    Direction,
    Usage,
)
from contextspy.analysis.classifier import classify
from contextspy.analysis.conversation import ContextMutation, InvocationIdentity
from contextspy.db import crud
from contextspy.db.models import Request


def _block_from_record(record: Any, content: str | None, *, direction: str | None = None) -> Block:
    return Block(
        direction=direction or record.direction,
        block_type=record.block_type,
        content=content or "",
        position=record.position,
        message_index=record.message_index,
        category=record.category,
        content_hash=record.content_hash,
        token_count=record.token_count,
        tool_name=record.tool_name,
        tool_call_id=record.tool_call_id,
        attrs=json.loads(record.attrs) if record.attrs else {},
    )


def _renumber_message_indices(entries: list[dict[str, Any]]) -> None:
    """Make message indices monotonic across inherited and current sources."""
    seen: dict[tuple[str | None, int], int] = {}
    next_index = 0
    for entry in entries:
        block = entry["block"]
        if block.message_index is None or block.message_index < 0:
            continue
        key = (entry.get("source_request_id"), block.message_index)
        if key not in seen:
            seen[key] = next_index
            next_index += 1
        block.message_index = seen[key]


def _current_entries(
    db: OrmSession, request: Request, input_blocks: list[Block],
) -> list[dict[str, Any]]:
    input_records = [
        record for record, _ in crud.get_raw_block_rows(
            db, request.id, direction=Direction.INPUT,
        )
    ]
    entries: list[dict[str, Any]] = []
    for position, block in enumerate(input_blocks):
        source_id = input_records[position].id if position < len(input_records) else None
        entries.append({
            "block": deepcopy(block),
            "source_request_id": request.id,
            "source_block_id": source_id,
            "provenance": "observed_current",
            "context_operation": "append_current_input",
        })
    return entries


def _inherited_entries(
    db: OrmSession, predecessor: Request, mutation: ContextMutation,
) -> tuple[list[dict[str, Any]], str]:
    entries: list[dict[str, Any]] = []
    snapshot_rows = crud.get_raw_context_rows(db, predecessor.id)
    if snapshot_rows:
        for record, content in snapshot_rows:
            attrs = json.loads(record.attrs) if record.attrs else {}
            if (
                record.block_type in mutation.drop_inherited_block_types
                and not attrs.get("conversation_item")
            ):
                continue
            entries.append({
                "block": _block_from_record(record, content, direction=Direction.INPUT),
                "source_request_id": record.source_request_id or predecessor.id,
                "source_block_id": record.source_block_id,
                "provenance": "inherited_input",
                "context_operation": "inherit_previous_input",
            })
        status = mutation.completeness
    else:
        # Legacy/pre-migration predecessor: inherit its observed request blocks
        # and mark the result partial rather than dropping all visible history.
        for record, content in crud.get_raw_block_rows(
            db, predecessor.id, direction=Direction.INPUT,
        ):
            attrs = json.loads(record.attrs) if record.attrs else {}
            if (
                record.block_type in mutation.drop_inherited_block_types
                and not attrs.get("conversation_item")
            ):
                continue
            entries.append({
                "block": _block_from_record(record, content, direction=Direction.INPUT),
                "source_request_id": predecessor.id,
                "source_block_id": record.id,
                "provenance": "inherited_input",
                "context_operation": "inherit_legacy_observed_input",
            })
        status = "partial"

    if mutation.include_previous_output:
        for record, content in crud.get_raw_block_rows(
            db, predecessor.id, direction=Direction.OUTPUT,
        ):
            entries.append({
                "block": _block_from_record(record, content, direction=Direction.INPUT),
                "source_request_id": predecessor.id,
                "source_block_id": record.id,
                "provenance": "inherited_output",
                "context_operation": "append_previous_output",
            })
    return entries, status


def _resolve_tool_names(entries: list[dict[str, Any]]) -> None:
    """Link results to calls after inherited and current blocks are combined."""
    calls = {
        entry["block"].tool_call_id: entry["block"].tool_name
        for entry in entries
        if entry["block"].block_type == BlockType.TOOL_CALL
        and entry["block"].tool_call_id
        and entry["block"].tool_name
    }
    custom_definitions: set[str] = set()
    for entry in entries:
        block = entry["block"]
        if block.block_type != BlockType.TOOL_DEFINITION or not block.tool_name:
            continue
        try:
            definition = json.loads(block.content)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(definition, dict) and definition.get("type") == "custom":
            custom_definitions.add(block.tool_name)
    for entry in entries:
        block = entry["block"]
        if (
            block.block_type == BlockType.TOOL_RESULT
            and not block.tool_name
            and block.tool_call_id in calls
        ):
            block.tool_name = calls[block.tool_call_id]
        elif (
            block.block_type == BlockType.TOOL_RESULT
            and not block.tool_name
            and block.attrs.get("response_item_type") == "custom_tool_call_output"
            and len(custom_definitions) == 1
        ):
            # Older canonical captures could omit the preceding custom call.
            # A single available custom definition is still an unambiguous
            # provider-visible join.
            block.tool_name = next(iter(custom_definitions))


def reconstruct_context(
    db: OrmSession,
    *,
    request: Request,
    analyzed: AnalyzedRequest,
    identity: InvocationIdentity,
    mutation: ContextMutation,
    predecessor: Request | None,
) -> list[dict[str, Any]]:
    """Build, persist, and account for one invocation's visible context."""
    entries: list[dict[str, Any]] = []
    status = mutation.completeness
    if mutation.inherit_previous:
        if predecessor is None:
            status = "unresolved"
            request.lineage_status = "unresolved_predecessor"
        else:
            inherited, inherited_status = _inherited_entries(db, predecessor, mutation)
            entries.extend(inherited)
            status = inherited_status
            request.lineage_status = "resolved"
    else:
        request.lineage_status = (
            "compacted" if mutation.completeness == "compacted" else "root"
        )

    entries.extend(_current_entries(db, request, analyzed.input_blocks))
    _resolve_tool_names(entries)
    _renumber_message_indices(entries)

    snapshot_blocks = [entry["block"] for entry in entries]
    reconstructed = classify(AnalyzedRequest(
        model=analyzed.model,
        input_blocks=snapshot_blocks,
        output_blocks=[],
        usage=Usage(),
    )).total_input

    provider_input = request.provider_input_tokens
    variance = provider_input - reconstructed if provider_input is not None else None
    unattributed = max(variance, 0) if variance is not None else None
    coverage = None
    if provider_input is not None:
        coverage = 100.0 if provider_input == 0 and reconstructed == 0 else (
            min(reconstructed / provider_input * 100.0, 100.0)
            if provider_input > 0 else 0.0
        )

    request.observed_input_tokens = request.tokens_total_input
    request.reconstructed_input_tokens = reconstructed
    request.unattributed_input_tokens = unattributed
    request.input_token_variance = variance
    request.context_coverage_pct = round(coverage, 1) if coverage is not None else None
    request.context_reconstruction_status = status
    crud.insert_context_snapshot(db, request.id, entries)
    db.flush()
    return entries


_CONTEXT_CATEGORIES = (
    "system_prompt",
    "tool_definitions",
    "tool_results",
    "file_contents",
    "conversation_history",
    "current_user_message",
    "assistant_prefill",
    "uncategorized",
)


def summarize_context_snapshot(
    blocks: list[dict[str, Any]], *, reconstructed_tokens: int | None = None,
) -> dict[str, Any]:
    """Return composition and tool statistics for a reconstructed snapshot."""
    by_category = {category: 0 for category in _CONTEXT_CATEGORIES}
    call_names: dict[str, str] = {}
    tools: dict[str, dict[str, Any]] = {}

    for block in blocks:
        category = block.get("category") or "uncategorized"
        if category not in by_category:
            category = "uncategorized"
        by_category[category] += int(block.get("token_count") or 0)
        if (
            block.get("block_type") == BlockType.TOOL_CALL
            and block.get("tool_call_id")
            and block.get("tool_name")
        ):
            call_names[block["tool_call_id"]] = block["tool_name"]

    for block in blocks:
        block_type = block.get("block_type")
        name = block.get("tool_name")
        if not name and block.get("tool_call_id"):
            name = call_names.get(block["tool_call_id"])
        if block_type == BlockType.TOOL_DEFINITION:
            name = name or "unknown"
            row = tools.setdefault(name, {
                "tool_name": name, "definition_tokens": 0, "result_tokens": 0,
            })
            row["definition_tokens"] += int(block.get("token_count") or 0)
        elif block_type == BlockType.TOOL_RESULT:
            name = name or "unknown"
            row = tools.setdefault(name, {
                "tool_name": name, "definition_tokens": 0, "result_tokens": 0,
            })
            row["result_tokens"] += int(block.get("token_count") or 0)

    visible_tokens = sum(by_category.values())
    total_tokens = reconstructed_tokens if reconstructed_tokens is not None else visible_tokens
    protocol_overhead = max(total_tokens - visible_tokens, 0)
    if protocol_overhead:
        by_category["protocol_overhead"] = protocol_overhead
    return {
        "composition": {
            "total_tokens": total_tokens,
            "visible_block_tokens": visible_tokens,
            "by_category": by_category,
        },
        "tools": sorted(tools.values(), key=lambda row: row["tool_name"]),
    }


__all__ = ["reconstruct_context"]


def reconcile_unresolved_descendants(db: OrmSession, predecessor: Request) -> int:
    """Resolve children captured before their predecessor, following the chain."""
    from contextspy.analysis.adapters import get_adapter
    from contextspy.analysis.conversation import get_conversation_adapter

    if not predecessor.provider_request_id:
        return 0
    resolved = 0
    queue = [predecessor]
    while queue:
        parent = queue.pop(0)
        if not parent.provider_request_id:
            continue
        for child in crud.get_unresolved_children(
            db, parent.provider, parent.provider_request_id,
        ):
            request_body: dict = {}
            response_body: dict = {}
            try:
                decoded = json.loads(child.raw_request_body or "{}")
                if isinstance(decoded, dict):
                    request_body = decoded
            except json.JSONDecodeError:
                pass
            try:
                decoded = json.loads(child.raw_response_body or "{}")
                if isinstance(decoded, dict):
                    response_body = decoded
            except json.JSONDecodeError:
                pass
            wire_adapter = get_adapter(child.endpoint)
            conversation_adapter = get_conversation_adapter(
                provider=child.provider,
                endpoint=child.endpoint,
                transport=child.transport,
                request_body=request_body,
            )
            if wire_adapter is None or conversation_adapter is None or not request_body:
                continue
            try:
                input_blocks, tool_map = wire_adapter.parse_request(request_body)
                output_blocks, usage = (
                    wire_adapter.parse_response(response_body)
                    if response_body else ([], Usage())
                )
            except Exception:
                continue
            identity = conversation_adapter.identify(
                provider=child.provider,
                agent=child.agent or "unknown",
                request_body=request_body,
                response_body=response_body,
            )
            mutation = conversation_adapter.context_mutation(
                request_body=request_body, identity=identity,
            )
            child_group = (
                crud.get_logical_request(db, child.logical_request_id)
                if child.logical_request_id else None
            )
            if (
                parent.logical_request_id
                and parent.session_id == child.session_id
                and child_group is not None
                and child_group.grouping_confidence == "singleton"
            ):
                old_group = crud.attach_request_to_logical(
                    db, child, parent.logical_request_id,
                )
                if old_group:
                    crud.refresh_logical_request(db, old_group)
            reconstruct_context(
                db,
                request=child,
                analyzed=AnalyzedRequest(
                    model=child.model,
                    input_blocks=input_blocks,
                    output_blocks=output_blocks,
                    usage=usage,
                    tool_call_map=tool_map,
                ),
                identity=identity,
                mutation=mutation,
                predecessor=parent,
            )
            if child.logical_request_id:
                crud.refresh_logical_request(db, child.logical_request_id)
            resolved += 1
            queue.append(child)
        crud.mark_forked_lineage(db, parent.provider, parent.provider_request_id)
    return resolved


__all__.append("reconcile_unresolved_descendants")
__all__.append("summarize_context_snapshot")
