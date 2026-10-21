# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Block activity: what kind of work a block's ``source_key`` represents.

Derived at read time from the persisted ``source_key`` (nothing is stored), so the tables below
can be refined without a migration. Vocabulary: read, search, edit, vcs, test, command, web,
orchestration, mcp, other. See plans/archive/wi0-data-foundation.md section 10.
"""
from __future__ import annotations

# Programs run through a wrapper tool such as bash:<program> or exec:<program>.
_PROGRAM_ACTIVITY: dict[str, str] = {
    **dict.fromkeys(("rg", "grep", "ag", "ack", "find", "fd", "locate"), "search"),
    **dict.fromkeys(("cat", "sed", "head", "tail", "less", "bat", "ls", "tree", "wc", "stat", "file", "nl"), "read"),
    **dict.fromkeys(("git", "gh", "hg", "svn"), "vcs"),
    **dict.fromkeys(("pytest", "jest", "vitest", "tox"), "test"),
    **dict.fromkeys(("apply_patch", "patch"), "edit"),
}

# Tool names (lower-cased) for the generic ``tool:<name>`` baseline.
_TOOL_ACTIVITY: dict[str, str] = {
    **dict.fromkeys(("read", "read_file", "view", "cat"), "read"),
    **dict.fromkeys(("grep", "glob", "search", "find", "ripgrep"), "search"),
    **dict.fromkeys(("edit", "write", "multiedit", "notebookedit", "str_replace_editor", "apply_patch"), "edit"),
    **dict.fromkeys(("bash", "shell", "exec", "run", "terminal"), "command"),
    **dict.fromkeys(("webfetch", "websearch", "fetch", "browser"), "web"),
    **dict.fromkeys(("task", "agent", "spawn_agent", "send_message", "wait_agent"), "orchestration"),
}

_NO_ACTIVITY = frozenset({"system", "user", "assistant", "reasoning"})


def activity_for(source_key: str | None) -> str | None:
    """Activity label for a source key; None for conversational keys and when there is no key."""
    if not source_key or source_key in _NO_ACTIVITY:
        return None
    namespace, _, name = source_key.partition(":")
    if not name:
        return "other"
    if namespace == "mcp":
        return "mcp"
    if namespace == "tool":
        return _TOOL_ACTIVITY.get(name.lower(), "other")
    # <wrapper>:<program>, plus the wrapper-level fallbacks multi / js
    return _PROGRAM_ACTIVITY.get(name, "command")
