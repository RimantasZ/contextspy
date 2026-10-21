# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Pure helpers for the hot-spots ranking (the SQL lives in ``db/hotspots_service.py``).

Hot spots rank the blocks of a scope (a conversation or a whole session) by the visible tokens they
add up to over all the requests that carry them. Nothing here needs block content: labels come from
stored structure (type, tool, source key, file path, provider item type), so everything works after a
session was archived. See plans/archive/hot-spots.md.
"""
from __future__ import annotations

from typing import Iterable

PREVIEW_CHARS = 120

# Packing of "the latest occurrence" into one integer so a single MAX() finds it in one pass:
# position in the scope * 2**32 + block row id.
POSITION_SHIFT = 1 << 32

_TYPE_LABELS = {
    "system_prompt": "System prompt",
    "user_message": "User message",
    "assistant_message": "Assistant message",
    "assistant_prefill": "Assistant prefill",
    "thinking": "Reasoning",
}
_TOOL_KIND = {"tool_definition": "definition", "tool_call": "call", "tool_result": "result"}


def unpack_latest(key: int) -> tuple[int, int]:
    """(scope position, block row id) of a packed latest-occurrence key."""
    return key // POSITION_SHIFT, key % POSITION_SHIFT


def count_runs(positions: Iterable[int]) -> int:
    """Number of maximal runs of consecutive scope positions (a block that vanishes and returns has several)."""
    runs = 0
    previous: int | None = None
    for position in sorted(set(positions)):
        if previous is None or position != previous + 1:
            runs += 1
        previous = position
    return runs


def share_pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def block_label(
    block_type: str, *, tool_name: str | None, source_key: str | None,
    file_path: str | None, provider_item_type: str | None,
) -> str:
    """A short, content-free name for a block."""
    kind = _TOOL_KIND.get(block_type)
    if kind is not None:
        base = source_key or tool_name or "tool"
        label = f"{base} {kind}"
        return f"{label} · {file_path}" if file_path else label
    if block_type == "other" or block_type not in _TYPE_LABELS:
        return provider_item_type or _TYPE_LABELS.get(block_type) or block_type
    return _TYPE_LABELS[block_type]


def preview_text(raw: str | None) -> str | None:
    """Whitespace-collapsed start of the content, at most PREVIEW_CHARS; None when there is no content."""
    if not raw:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1].rstrip() + "…"
