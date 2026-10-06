# Plan 4a: Capture the file a block is about (`blocks.file_path`)

Status: **reviewed and decided 2026-10-06; not started.** Prerequisite of [hot-spots.md](hot-spots.md) (Plan 4b). Part of [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md).

## Decision (user, 2026-10-06) and its consequence
Hot spots should be able to say "this file's contents are carried N times and cost T tokens". That needs the file a read/edit tool targeted. The user decided to **store full file paths**
("the retention policy was a mistake anyway"), with a note to **obfuscate paths later if it ever becomes an issue**.

This deliberately **reverses one rule**: until now parsers recorded program names only and never arguments or paths (WI-0 §4.3 privacy rule, `analysis/sources.py` docstring, `docs/development.md`, `tests/test_classification.py::test_parsers_never_record_arguments`).
The reversal is narrow: only a *file path that a known read/edit tool targets* is stored, in one dedicated column. Every other argument, command text, URL, pattern and secret stays unstored.
Paths now **persist after archive** (the column is on block rows, which archive keeps).

**Obfuscation hook (required, for the future):** every path goes through one function, `analysis/paths.py: normalize_file_path(raw) -> str | None`. Today it only validates and trims; if paths ever become a problem
(shared databases, screenshots, exports) obfuscation (basename only, per-database salted hash, a `[privacy]` setting) is a change inside that function plus a re-derivation migration, nothing else. Keep it the only place that writes `file_path`.

## Design

### Schema (v10, additive + re-derivation)
- `blocks.file_path TEXT NULL` + `idx_blocks_file_path` (`db/models.py`, `db/database.py:_migrate()` additive column and index).
- `SCHEMA_VERSION` 9 → 10 with `_migrate_to_v10`: re-run the v9 phase-A derivation for requests with `classifier_version < 2` (`CLASSIFIER_VERSION` becomes 2) so source keys and `file_path` are recomputed from retained tool-call content. Reuse `_migrate_to_v9`'s batching/progress code (factor the shared loop out); no JSON re-parsing needed (phase B stays v9's). Historical coverage is partial by nature (tool-call text is only available where block content was not purged); say so in the changelog.

### Extraction (`analysis/sources.py`, `analysis/paths.py`)
- `SourceInfo` gains `file_path: str | None` (and the detail may list further paths: `{"files": [...]}`, max 20). `resolve_sources` assigns `file_path` to **tool calls** (from the parser) and to **tool results by inheritance** through `tool_call_id` (same mechanism as `source_key`); tool definitions get none.
- **Generic structured-argument parser** (any agent/provider, any tool whose arguments are a JSON object): applies to calls whose tool name's activity (`activity.py`) is `read` or `edit`, or whose name is in a small explicit list (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `read_file`, `write_file`, `edit_file`, `str_replace_editor`, `view`, `open_file`, ...). Takes the first string value under the keys `file_path`, `filepath`, `path`, `filename`, `file`, `notebook_path` (in that order). Rejects: not a string, empty, > 1024 chars, contains NUL/newline, contains `://`, contains glob characters (`*`, `?`), or looks like a directory-only query for list tools. No other argument is read.
- **Shell-style tools** (`Bash`, Codex `exec`/`js` via the existing parsers) get a *closed* list of programs whose positional path argument is unambiguous: `cat`, `head`, `tail`, `nl`, `bat`, `less`, `wc`, `sed` (when the script is `-n`/`-e` style, last argument is the file), `stat`, `file`. Not `rg`/`grep`/`find`/`curl`/`git`/anything with ambiguous or secret-bearing arguments. A `cmd` with several matching segments yields the first path, the rest in `detail["files"]`.
- **`apply_patch`** (Codex JavaScript snippets and Claude-style patch tools): the headers `*** Add File:`, `*** Update File:`, `*** Delete File:` give the paths (first stored, all in `detail["files"]`); the patch body is never stored.
- All results pass `normalize_file_path` (trim, strip surrounding quotes, collapse `//`, keep case, no resolution against any working directory, no `~` expansion). Relative and absolute spellings of the same file are **not** unified (documented limitation).
- Persist via `crud.insert_blocks` (`file_path=b.file_path`); `Block.file_path` field; `BlockRecord.to_dict` returns `file_path`; `BlockInspector` shows a "File" row; `RequestBlock.file_path` in `client.ts`.

### Docs to change together
`docs/development.md` and the `sources.py`/`purpose.py` docstrings ("program names only" → "program names, plus the file path a read/edit tool targets in `blocks.file_path`"), `SPEC.md` (schema, §5.2), the WI-0 §4.3 privacy rule (mark superseded), `README.md` (one sentence: file paths of read/edit tool calls are stored locally), `docs/changelog.md`, `docs/faq.md`.

## Tests
1. Generic parser: each key name, precedence, rejection cases (URL, glob, newline, over-long, non-string, nested object), tools not in the list ignored, no other argument leaks into `file_path` or `detail`.
2. Shell: each allowed program with typical argument shapes (`sed -n '1,5p' a/b.py`, `head -n 20 f`, `cat a b` → first + files), refused programs (`rg x src`, `curl ... secret`) produce no path; the existing secret-argument test still passes (nothing but program names and the path leaves the call).
3. `apply_patch` headers (single and multiple files); patch body absent from every stored value.
4. Results inherit their call's path; definitions and unrelated blocks have none; per adapter (Anthropic, chat, Responses, Ollama) with synthetic fixtures.
5. `normalize_file_path` unit table (quotes, `//`, length, control characters) and "single choke point" test (monkeypatch it to a sentinel and confirm every stored path comes from it).
6. Capture: a request through `_save_request` persists `file_path` on the call and its result.
7. Migration v10: re-derives only requests below version 2, writes `file_path` where retained content allows, leaves purged ones NULL, is idempotent, preserves existing `attrs`; `pending == [..., 10]` expectations updated; v9→v10 on a copy of a real DB timed.
8. API/UI: `file_path` in block payloads; inspector row shown/hidden.

## Open questions
None blocking. Later: obfuscation setting (see hook); unifying relative/absolute spellings (needs a working directory the proxy does not see).
