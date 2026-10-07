# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""What produced a block: a compact, provider-neutral ``source_key``.

Keys are persisted at capture time (``blocks.source_key``) because tool-call arguments are
purged by archive/retention. See plans/archive/wi0-data-foundation.md section 4.

Grammar: bare keys ``system``, ``user``, ``assistant``, ``reasoning``, ``other``; otherwise
``<namespace>:<name>``:

* ``tool:<name>``            generic baseline for any provider's tool definition/call/result
* ``mcp:<server>/<tool>``    tools named ``mcp__<server>__<tool>``
* ``<wrapper>:<program>``    produced by a registered parser, e.g. ``bash:git``, ``exec:rg``;
                             ``<wrapper>:multi`` / ``<wrapper>:js`` when it could not say more

Parsers record program names. The only argument ever read is the file a known read/edit tool
targets, stored separately in ``blocks.file_path`` (``analysis/paths.py`` is the single choke point);
command text, URLs, patterns and patch bodies are never stored. See plans/archive/file-paths.md.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from contextspy.analysis.blocks import BlockType, BlockView
from contextspy.analysis.paths import MAX_FILES, normalize_file_path, patch_file_paths, structured_file_path

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
    # The file a read/edit tool call targets (first one; all of them in detail["files"] when several).
    # Tool results inherit it from their call.
    file_path: str | None = None


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
    key and file of the call it answers (found by ``tool_call_id``); everything else is the baseline.
    """
    call_infos: dict[str, SourceInfo] = {}
    result: list[SourceInfo | None] = [None] * len(blocks)
    for index, block in enumerate(blocks):
        try:
            if block.block_type == BlockType.TOOL_CALL:
                info = _with_files(_parsed_call_source(block, agent) or baseline_source(block), block, agent)
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
                # key and file only: the rest of the detail stays on the call
                result[index] = SourceInfo(inherited.key, file_path=inherited.file_path)
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


def _segment_words(segment: str) -> list[str]:
    """The words of one command segment starting at its program (prefix wrappers and ``VAR=x`` skipped)."""
    import shlex

    segment = segment.strip().lstrip("({").strip()
    if not segment:
        return []
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
    return words[index:]


def shell_program(command: str) -> str | None:
    """The program a shell command line mainly runs, e.g. ``git`` for ``cd x && git diff | head``.

    Looks at the first meaningful segment only (skipping ``cd``/``export``/... segments, leading
    ``VAR=value`` assignments and ``sudo``/``env``/``time``-style prefixes) and returns the
    basename of its first word, or None when nothing sensible is found.
    """
    for segment in _split_segments(command[:_MAX_SCAN_CHARS]):
        words = _segment_words(segment)
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1].rstrip(")}")
        if program in _SKIP_PROGRAMS:
            continue
        return program if _PROGRAM.match(program) else None
    return None


# ---------------------------------------------------------------------------
# Shell command helper (files of a closed list of read-only programs)
# ---------------------------------------------------------------------------

# Programs whose positional arguments are unambiguously files, with the options that consume the
# next word. Everything else (rg, grep, find, curl, git, ...) has ambiguous or secret-bearing arguments.
_FILE_READERS: dict[str, frozenset[str]] = {
    "cat": frozenset(), "nl": frozenset({"-s", "-w", "-b", "-n", "-v", "-i", "-l"}),
    "head": frozenset({"-n", "-c", "--lines", "--bytes"}), "tail": frozenset({"-n", "-c", "--lines", "--bytes"}),
    "less": frozenset({"-p", "-P", "-b", "-h", "-j", "-x", "-y", "-z"}), "wc": frozenset({"--files0-from"}),
    "bat": frozenset({"-l", "--language", "-r", "--line-range", "--theme", "--style", "--color", "-H"}),
    "stat": frozenset({"-c", "--format", "--printf"}), "file": frozenset({"-f", "-m", "-e", "--mime-type"}),
}
_REDIRECT = re.compile(r"^\d*[<>]|^&>")
_UNSAFE_WORD = re.compile(r"[$`()|;&{}]")


def _positionals(words: list[str], value_options: frozenset[str]) -> tuple[list[str], set[str]]:
    """Non-option words of a command (redirections skipped) and the options it was given."""
    positional: list[str] = []
    options: set[str] = set()
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
        elif _REDIRECT.match(word):
            skip_next = word.strip("0123456789") in {">", ">>", "<", "&>"}
        elif word == "--":
            continue
        elif word.startswith("-") and len(word) > 1:
            options.add(word)
            skip_next = word in value_options
        elif word != "-" and not _UNSAFE_WORD.search(word):
            positional.append(word)
    return positional, options


def _segment_files(words: list[str]) -> list[str | None]:
    program = words[0].rsplit("/", 1)[-1]
    if program == "sed":
        positional, options = _positionals(words[1:], frozenset({"-e", "-f", "--expression", "--file"}))
        if "-i" in options or any(o.startswith(("-i", "--in-place")) for o in options):
            return []  # in-place editing is a different, ambiguous operation
        scripted = bool(options & {"-e", "--expression", "-f", "--file"})
        if not ({"-n", "--quiet", "--silent"} & options or scripted):
            return []
        return [normalize_file_path(w) for w in positional[0 if scripted else 1:]]
    value_options = _FILE_READERS.get(program)
    if value_options is None:
        return []
    positional, _ = _positionals(words[1:], value_options)
    return [normalize_file_path(w) for w in positional]


def shell_file_paths(command: str) -> list[str]:
    """Files named by the ``cat``/``head``/``tail``/``sed -n``/... segments of a shell command line."""
    found: list[str | None] = []
    for segment in _split_segments(command[:_MAX_SCAN_CHARS]):
        words = _segment_words(segment)
        if words:
            found.extend(_segment_files(words))
    unique: list[str] = []
    for path in found:
        if path and path not in unique:
            unique.append(path)
    return unique[:MAX_FILES]


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


def _codex_steps(snippet: str) -> list[tuple[str, str]]:
    """The ``("cmd", command)`` and ``("fn", name)`` steps of a Codex snippet, in order."""
    steps: list[tuple[str, str]] = []
    matches = list(_TOOLS_CALL.finditer(snippet))
    for position, match in enumerate(matches):
        function = match.group(1)
        if function == "exec_command":
            end = matches[position + 1].start() if position + 1 < len(matches) else len(snippet)
            argument = _CMD_START.search(snippet, match.end(), end)
            if argument:
                command = _js_string(snippet, argument.end(), argument.group(1))
                if command is not None:
                    steps.append(("cmd", command))
        else:
            steps.append(("fn", function))
    if not matches and "*** Begin Patch" in snippet:
        steps.append(("fn", "apply_patch"))
    return steps


def _parse_codex_exec(block: BlockView, agent: str | None) -> SourceInfo | None:
    snippet = (block.content or "")[:_MAX_SCAN_CHARS]
    programs: list[str] = []

    def add(program: str | None) -> None:
        if program and _PROGRAM.match(program) and program not in programs:
            programs.append(program)

    for kind, value in _codex_steps(snippet):
        add(shell_program(value) if kind == "cmd" else value)

    if not programs:
        return SourceInfo("exec:js")
    if len(programs) == 1:
        return SourceInfo(f"exec:{programs[0]}")
    return SourceInfo("exec:multi", {"calls": programs[:_MAX_DETAIL_CALLS]})


# ---------------------------------------------------------------------------
# Files targeted by a tool call (stored in blocks.file_path; see analysis/paths.py)
# ---------------------------------------------------------------------------

def _call_files(block: BlockView, agent: str | None) -> list[str]:
    """Files a tool call reads or edits; empty when unknown or the content was purged."""
    content = (block.content or "")[:_MAX_SCAN_CHARS]
    if not content:
        return []
    name = (block.tool_name or "").lower()
    if name in {"bash"}:
        try:
            arguments = json.loads(content)
        except (TypeError, ValueError):
            return []
        command = arguments.get("command", arguments.get("cmd")) if isinstance(arguments, dict) else None
        return shell_file_paths(command) if isinstance(command, str) else []
    if agent == "codex" and (block.tool_name in ("exec", "js")):
        files: list[str | None] = [
            *(path for kind, value in _codex_steps(content) if kind == "cmd" for path in shell_file_paths(value)),
            *patch_file_paths(content),
        ]
        return [p for i, p in enumerate(files) if p and p not in files[:i]][:MAX_FILES]
    if name == "apply_patch":
        return patch_file_paths(content)
    path = structured_file_path(block.tool_name, content)
    return [path] if path else []


def _with_files(info: SourceInfo, block: BlockView, agent: str | None) -> SourceInfo:
    try:
        files = _call_files(block, agent)
    except Exception:  # a heuristic must never break capture or a backfill
        logger.debug("file extraction failed for %s", block.tool_name, exc_info=True)
        return info
    if not files:
        return info
    detail = {**(info.detail or {}), "files": files} if len(files) > 1 else info.detail
    return SourceInfo(info.key, detail, files[0])


register_source_parser(
    agents=None, tool_names=frozenset({"Bash", "bash"}), parser=_parse_bash,
)
register_source_parser(
    agents=frozenset({"codex"}), tool_names=frozenset({"exec", "js"}), parser=_parse_codex_exec,
)
