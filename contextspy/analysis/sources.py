# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""What produced a block: a compact, provider-neutral ``source_key``.

Keys are persisted at capture time (``blocks.source_key``) because tool-call arguments are
purged by archive/retention. See plans/wi0-data-foundation.md section 4.

Grammar: bare keys ``system``, ``user``, ``assistant``, ``reasoning``, ``other``; otherwise
``<namespace>:<name>``:

* ``tool:<name>``            generic baseline for any provider's tool definition/call/result
* ``mcp:<server>/<tool>``    tools named ``mcp__<server>__<tool>``
* ``<wrapper>:<program>``    produced by a registered parser, e.g. ``bash:git``, ``exec:rg``;
                             ``<wrapper>:multi`` / ``<wrapper>:js`` when it could not say more

Parsers only ever record program names, never arguments, paths or other command content.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from contextspy.analysis.blocks import BlockType, BlockView

logger = logging.getLogger(__name__)

_MCP_NAME = re.compile(r"^mcp__(?P<server>.+?)__(?P<tool>.+)$")
_PROGRAM = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_MAX_SCAN_CHARS = 1_000_000
_MAX_DETAIL_CALLS = 20


@dataclass(frozen=True)
class SourceInfo:
    key: str
    # JSON-serialisable refinement stored in the block's attrs["source"], e.g. {"calls": ["rg", "sed"]}.
    detail: dict | None = None


# ---------------------------------------------------------------------------
# Baseline (every provider)
# ---------------------------------------------------------------------------

def mcp_key(tool_name: str | None) -> str | None:
    """``mcp__github__create_issue`` -> ``mcp:github/create_issue``; None for other names."""
    match = _MCP_NAME.match(tool_name or "")
    return f"mcp:{match['server']}/{match['tool']}" if match else None


_BARE_KEYS = {
    BlockType.SYSTEM_PROMPT: "system",
    BlockType.USER_MESSAGE: "user",
    BlockType.ASSISTANT_MESSAGE: "assistant",
    BlockType.ASSISTANT_PREFILL: "assistant",
    BlockType.THINKING: "reasoning",
}
_TOOL_TYPES = (BlockType.TOOL_DEFINITION, BlockType.TOOL_CALL, BlockType.TOOL_RESULT)


def baseline_source(block: BlockView) -> SourceInfo:
    block_type = block.block_type
    if block_type in _BARE_KEYS:
        return SourceInfo(_BARE_KEYS[block_type])
    if block_type in _TOOL_TYPES:
        return SourceInfo(mcp_key(block.tool_name) or f"tool:{block.tool_name or 'unknown'}")
    return SourceInfo("other")


# ---------------------------------------------------------------------------
# Parser registry (optional, agent- or tool-specific refinements)
# ---------------------------------------------------------------------------

SourceParser = Callable[[BlockView, "str | None"], "SourceInfo | None"]
_PARSERS: list[tuple[frozenset[str] | None, frozenset[str], SourceParser]] = []


def register_source_parser(
    *, agents: frozenset[str] | None, tool_names: frozenset[str], parser: SourceParser,
) -> None:
    """Register ``parser(block, agent)`` for tool-call blocks of ``tool_names``.

    ``agents=None`` matches every agent. A parser returns None to fall back to the baseline;
    exceptions are caught and also fall back.
    """
    _PARSERS.append((agents, tool_names, parser))


def _parsed_call_source(block: BlockView, agent: str | None) -> SourceInfo | None:
    if not block.content:
        return None  # purged or empty: nothing to parse
    for agents, tool_names, parser in _PARSERS:
        if block.tool_name not in tool_names or (agents is not None and agent not in agents):
            continue
        try:
            info = parser(block, agent)
        except Exception:  # a heuristic must never break capture or a backfill
            logger.debug("source parser failed for %s", block.tool_name, exc_info=True)
            continue
        if info is not None:
            return info
    return None


def resolve_sources(blocks: Sequence[BlockView], *, agent: str | None) -> list[SourceInfo]:
    """One ``SourceInfo`` per block, same order. Pure and never raises.

    Tool calls are parsed by a registered parser when one applies; a tool result inherits the
    key of the call it answers (found by ``tool_call_id``); everything else is the baseline.
    """
    call_infos: dict[str, SourceInfo] = {}
    result: list[SourceInfo | None] = [None] * len(blocks)
    for index, block in enumerate(blocks):
        try:
            if block.block_type == BlockType.TOOL_CALL:
                info = _parsed_call_source(block, agent) or baseline_source(block)
                result[index] = info
                if block.tool_call_id:
                    call_infos.setdefault(block.tool_call_id, info)
        except Exception:
            logger.debug("source resolution failed", exc_info=True)
    for index, block in enumerate(blocks):
        if result[index] is not None:
            continue
        try:
            inherited = (
                call_infos.get(block.tool_call_id)
                if block.block_type == BlockType.TOOL_RESULT and block.tool_call_id else None
            )
            if inherited is not None and not (block.tool_name and inherited.key == "tool:unknown"):
                result[index] = SourceInfo(inherited.key)  # key only: detail stays on the call
            else:
                result[index] = baseline_source(block)
        except Exception:
            result[index] = SourceInfo("other")
    return [info or SourceInfo("other") for info in result]


# ---------------------------------------------------------------------------
# Shell command helper (program names only)
# ---------------------------------------------------------------------------

_SKIP_PROGRAMS = frozenset({"cd", "pushd", "popd", "export", "unset", "set", "umask", "source", ".", ":", "true"})
# Prefix wrappers and the options that consume the following word (so it is not mistaken for the program).
_PREFIX_WRAPPERS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t"}),
    "env": frozenset({"-u", "-C", "-S", "--unset", "--chdir"}),
    "nice": frozenset({"-n"}),
    "time": frozenset(), "nohup": frozenset(), "command": frozenset(), "exec": frozenset(),
}
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_segments(command: str) -> list[str]:
    """Split on ``&&``, ``||``, ``;``, ``|`` and newlines that are not inside quotes."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        char = command[i]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "\\" and i + 1 < len(command):
            current.append(char)
            current.append(command[i + 1])
            i += 1
        elif char in ";\n" or char == "|" or (char == "&" and command[i + 1:i + 2] == "&"):
            segments.append("".join(current))
            current = []
            if char in "|&" and command[i + 1:i + 2] == char:
                i += 1
        else:
            current.append(char)
        i += 1
    segments.append("".join(current))
    return segments


def shell_program(command: str) -> str | None:
    """The program a shell command line mainly runs, e.g. ``git`` for ``cd x && git diff | head``.

    Looks at the first meaningful segment only (skipping ``cd``/``export``/... segments, leading
    ``VAR=value`` assignments and ``sudo``/``env``/``time``-style prefixes) and returns the
    basename of its first word, or None when nothing sensible is found.
    """
    import shlex

    for segment in _split_segments(command[:_MAX_SCAN_CHARS]):
        segment = segment.strip().lstrip("({").strip()
        if not segment:
            continue
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            words = segment.split()
        index = 0
        while index < len(words):
            word = words[index]
            wrapper = _PREFIX_WRAPPERS.get(word.rsplit("/", 1)[-1])
            if _ENV_ASSIGNMENT.match(word):
                index += 1
            elif wrapper is not None:
                index += 1
                while index < len(words) and (words[index].startswith("-") or _ENV_ASSIGNMENT.match(words[index])):
                    index += 2 if words[index] in wrapper else 1
            else:
                break
        if index >= len(words):
            continue
        program = words[index].rsplit("/", 1)[-1].rstrip(")}")
        if program in _SKIP_PROGRAMS:
            continue
        return program if _PROGRAM.match(program) else None
    return None


# ---------------------------------------------------------------------------
# Parser: JSON-argument shell tools (Claude Code "Bash" and similar)
# ---------------------------------------------------------------------------

def _parse_bash(block: BlockView, agent: str | None) -> SourceInfo | None:
    try:
        arguments = json.loads(block.content or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None
    command = arguments.get("command", arguments.get("cmd"))
    if not isinstance(command, str):
        return None
    program = shell_program(command)
    if program is None:
        return None
    return SourceInfo(f"{(block.tool_name or 'bash').lower()}:{program}")


# ---------------------------------------------------------------------------
# Parser: Codex "exec"/"js" tool calls, which are JavaScript snippets
# ---------------------------------------------------------------------------

_TOOLS_CALL = re.compile(r"tools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_CMD_START = re.compile(r"\bcmd\s*:\s*([\"'`])")
_JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "`": "`"}


def _js_string(text: str, start: int, quote: str) -> str | None:
    """Read a JS string literal body starting just after its opening quote; None if unusable."""
    out: list[str] = []
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            out.append(_JS_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if char == quote:
            return "".join(out)
        if quote == "`" and char == "$" and text[i + 1:i + 2] == "{":
            return None  # template expression: not statically knowable
        out.append(char)
        i += 1
    return None


def _parse_codex_exec(block: BlockView, agent: str | None) -> SourceInfo | None:
    snippet = (block.content or "")[:_MAX_SCAN_CHARS]
    programs: list[str] = []

    def add(program: str | None) -> None:
        if program and _PROGRAM.match(program) and program not in programs:
            programs.append(program)

    matches = list(_TOOLS_CALL.finditer(snippet))
    for position, match in enumerate(matches):
        function = match.group(1)
        if function == "exec_command":
            end = matches[position + 1].start() if position + 1 < len(matches) else len(snippet)
            argument = _CMD_START.search(snippet, match.end(), end)
            if argument:
                command = _js_string(snippet, argument.end(), argument.group(1))
                if command is not None:
                    add(shell_program(command))
        else:
            add(function)
    if not matches and "*** Begin Patch" in snippet:
        add("apply_patch")

    if not programs:
        return SourceInfo("exec:js")
    if len(programs) == 1:
        return SourceInfo(f"exec:{programs[0]}")
    return SourceInfo("exec:multi", {"calls": programs[:_MAX_DETAIL_CALLS]})


register_source_parser(
    agents=None, tool_names=frozenset({"Bash", "bash"}), parser=_parse_bash,
)
register_source_parser(
    agents=frozenset({"codex"}), tool_names=frozenset({"exec", "js"}), parser=_parse_codex_exec,
)
