# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""The file a read/edit tool call targets, persisted as ``blocks.file_path``.

``normalize_file_path`` is the **only** function that decides what is stored: every path found
by any extractor (structured tool arguments, shell commands, patch headers) goes through it.
Today it validates and trims. If paths ever become a privacy problem (shared databases, exports),
obfuscation (basename only, a salted hash, a setting) is a change inside that function plus a
re-derivation migration, nothing else. See plans/archive/file-paths.md.

Only a path that a known read/edit tool targets is read; no other argument (command text, URLs,
patterns, patch bodies, secrets) is ever returned from this module.
"""
from __future__ import annotations

import json
import re

from contextspy.analysis.activity import activity_for

MAX_PATH_CHARS = 1024
MAX_FILES = 20
_MAX_SCAN_CHARS = 1_000_000

_MULTI_SLASH = re.compile(r"/{2,}")
_QUOTES = "'\"`"


def normalize_file_path(raw: object) -> str | None:
    """Validate and trim a candidate path; None when it is not a plausible single file path.

    Trims whitespace and one pair of surrounding quotes, collapses repeated ``/``. Keeps case; does
    not resolve against any working directory and does not expand ``~``. Rejects non-strings,
    empty or over-long values, control characters, URLs, glob patterns, trailing ``/`` (a
    directory), and ``.``/``..``/``/``.
    """
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in _QUOTES:
        path = path[1:-1].strip()
    if not path or len(path) > MAX_PATH_CHARS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None
    if "://" in path or "*" in path or "?" in path:
        return None
    path = _MULTI_SLASH.sub("/", path)
    if path.endswith("/") or path in {".", "..", "/"}:
        return None
    return path


def _unique(paths: list[str | None]) -> list[str]:
    seen: list[str] = []
    for path in paths:
        if path and path not in seen:
            seen.append(path)
        if len(seen) >= MAX_FILES:
            break
    return seen


# ---------------------------------------------------------------------------
# Structured arguments: any provider, any tool whose arguments are a JSON object
# ---------------------------------------------------------------------------

_PATH_KEYS = ("file_path", "filepath", "path", "filename", "file", "notebook_path")
# Names that read or edit a file but are not (all) known to activity.py.
_FILE_TOOLS = frozenset({
    "read", "write", "edit", "multiedit", "notebookedit", "read_file", "write_file", "edit_file",
    "create_file", "str_replace_editor", "str_replace_based_edit_tool", "view", "view_file", "open_file",
})


def is_file_tool(tool_name: str | None) -> bool:
    """Whether a tool named like this reads or edits the file its arguments name."""
    if not tool_name:
        return False
    name = tool_name.lower()
    return name in _FILE_TOOLS or activity_for(f"tool:{tool_name}") in ("read", "edit")


def structured_file_path(tool_name: str | None, content: str | None) -> str | None:
    """The file named by a read/edit tool call's JSON-object arguments; None for anything else."""
    if not is_file_tool(tool_name) or not content:
        return None
    try:
        arguments = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            path = normalize_file_path(value)
            if path:
                return path
    return None


# ---------------------------------------------------------------------------
# apply_patch headers (works on raw text, also inside JSON or JavaScript string literals)
# ---------------------------------------------------------------------------

_PATCH_HEADER = re.compile(r"\*\*\* (?:Add|Update|Delete) File: ([^\n\r\\\"'`]+)")


def patch_file_paths(text: str | None) -> list[str]:
    """Paths named by ``*** Add/Update/Delete File:`` headers; the patch body is never returned."""
    if not text or "*** " not in text:
        return []
    return _unique([normalize_file_path(m.group(1)) for m in _PATCH_HEADER.finditer(text[:_MAX_SCAN_CHARS])])
