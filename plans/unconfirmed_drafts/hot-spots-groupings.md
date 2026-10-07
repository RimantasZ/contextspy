# Idea 9: More hot-spots groupings (types, commands, invocations)

Status: **DRAFT / idea, not reviewed.** Raised by the user on 2026-10-06 while looking at the implemented Hot spots page ([../archive/hot-spots.md](../archive/hot-spots.md), Plan 4b): "group by block type only, group by command, group by command and arguments". Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). To be reviewed when post-release work starts (roadmap D23). **One timing point needs a decision before the release: see "Schema timing".**

> **REMINDER FOR THE AGENT WORKING ON THIS PLAN (user request, 2026-10-06): when work on this plan starts, stop and ask the user for a decision before doing anything else.** The user was not convinced by the conclusion that "invocations" mostly duplicate what the Blocks view shows (call row + result row; the only real gain being the merge when a command's output changes, and shell commands without a file path), and asked to be prompted. Ask: (1) is the call+result unit worth a new grouping, or is a "paired call/result" line in the Blocks view enough, or neither; (2) which of options 1-3 below, and (3) the schema-timing question if it is still open at that time. Do not mark this plan low priority or drop it on your own; the user has not decided.

## What exists today (so the gap is clear)
Hot spots groups by `group=block|source|file` (`db/hotspots_service.py: aggregate_select`):
- **Blocks:** exact content hash. A tool call's text is the command *and* its arguments, so two calls with different arguments are two rows, and the same call repeated in history merges into one row. The label shows program and file (`exec:sed call · a.py`); the arguments are only visible in the 120-character preview while the text is still stored.
- **Sources:** `source_key`, i.e. the program name (`bash:sed`, `exec:rg`, `tool:Read`); arguments are never part of it. Calls, results and tool definitions of a tool can fall under different keys (a tool *definition* keeps the baseline `tool:<name>`; calls/results carry the parsed `bash:git`; WI-0 §17 slice 3).
- **Files:** `file_path` (read/edit tool calls and their results).
Arguments are deliberately **not stored** except the file path (D18), so the "arguments" of a command exist only as the call block's text (retained text) and as that text's hash (kept after archive).

## The three requested modes

### A. Types: group by block type (optionally with category)
`GROUP BY b.block_type` (or `(block_type, category)`). Trivial: same single pass, same CROSS JOIN shape, row identity = the type. Overlaps the category donut/context bar of the session overview, but adds what the overview lacks: per conversation, occurrence and request counts, tokens carried over time, the in-context filter. Lowest value and lowest cost; could simply be a preset of the existing category/type filters. Note: tool definitions, calls and results are three types, so "tool results" cost is visible here.

### B. Commands: group by program (without arguments)
Already exists as **Sources**; the work is naming and scope, not computation. Open points: rename to "Commands"? (Sources also contains `system`, `user`, `assistant`, `reasoning`, `other`, which are not commands); optionally attribute a tool's **definition** tokens to the tool (`tool:Bash` definition vs `bash:git` calls) with a toggle; where call text was purged before keys were derived, Codex calls are only `tool:exec`/`tool:js` (a data limit).

### C. Invocations: group by command and arguments, call and result as one unit
Two readings:
1. **Calls only:** Blocks filtered to block type "tool call": the cost of the command text alone. Available now (the plan's `block_type=tool_call` filter), no work.
2. **Call plus its result (recommended meaning):** "what did `cat plans/x.md` cost in total": the call's tokens plus its result's tokens, every time they were carried, and merging runs whose output differed (a file that changed between two reads). Today call and result are separate rows with separate hashes, so this is **not** available.

#### Design options for reading 2
- **Option 1, read-time linking (no schema change).** Results share `tool_call_id` with their call inside a request (Anthropic `tool_use_id`, Chat `tool_call_id`, Responses `call_id`; **Ollama's adapter emits no tool blocks, so no invocations there**). Materialise the scope's tool blocks once into a temp table (one scope-outer-loop pass restricted to `tool_call`/`tool_result`, the "variant D" of the 4b performance measurements: 0.02-1.3 s) and self-join on `(request_id, tool_call_id)` to give each result its call's `content_hash`; then aggregate by that hash. Keeps the plan-test query shape if the materialisation step is the only place touching `blocks`; roughly one extra pass over tool blocks. Needs a measurement on the 4,032-request sample before approval (budget: stay near the ~1.1 s of the block grouping).
- **Option 2, persist the link at capture (schema v11).** A new nullable column on result blocks, e.g. `blocks.call_hash` (the call block's `content_hash`), set by `resolve_sources` (which already pairs results to calls by `tool_call_id` to inherit `source_key`/`file_path`) and backfilled by the shared `_backfill_classification` loop (bump `CLASSIFIER_VERSION`; v10 shows the pattern). Grouping becomes a plain `GROUP BY COALESCE(call_hash, content_hash)` over call and result blocks, one pass, no self-join, and it **survives archive** (hashes are kept). Costs a migration and one more indexed-or-not column.
- **Option 3, a normalised command key.** Hash only the *command string* (Codex `cmd`, Bash `command`, structured tool arguments minus volatile fields such as `workdir`/`max_output_tokens`) instead of the whole call text, so equivalent calls merge. Better merging, but it needs capture-time derivation and its own stored column (privacy note: a hash, not text), and is easy to get subtly wrong per agent (D16: agent-specific logic is a plug-in). Treat as a refinement of option 2, not a first step.

My lean: option 1 for a first version if the measurement is acceptable (no migration), option 2 if it is not or if the user wants archived sessions to keep invocation grouping fast. Both give the same numbers.

## Schema timing (decision needed before the release)
If option 2 (or 3) is wanted at all, adding the column **before the first release** means users upgrading from 0.5.4 run one `db-upgrade` that goes 8 to 11; adding it **after** means every user who already upgraded to 10 runs another data migration (~100 s per 7k requests; the v10 pass took 104 s on the sample). If there is any chance of option 2, deciding now is cheaper. If option 1 is enough, there is nothing to decide.

## UI sketch (wait for the author's styling remarks first, D9)
The group control would grow from three to five or six options (Blocks, Types, Sources/Commands, Invocations, Files). Consider a dropdown or a two-level control ("Group by" select; "Calls only / Call + result" as a sub-option of Invocations) rather than more segments. Invocation rows: label = program (+ file), meta = occurrences, requests, tokens each for call and for result, first/last seen; click opens the latest occurrence with the *call* block selected (`?block=`); the preview shows the command start while text is retained.

## Tests (when implemented)
Counts per mode on small fixtures; call and result merged under one row with `call_tokens` and `result_tokens`; results of differing output merge under one call hash; unpaired calls (no result yet) and unpaired results (`tool_call_id` missing, Ollama) handled; same command in two conversations; archived and purged data give identical numbers; `EXPLAIN QUERY PLAN` still shows the scope as outer loop and no scan of `idx_blocks_content_hash`; constant statement count; for option 2: migration re-derives only requests below the new classifier version and is idempotent.

## Open questions
1. Is "call plus result as one unit" the reading you want for *invocations*, or is the calls-only reading (already available) enough?
2. Should a tool's definition cost be attributed to the tool in the Commands view (toggle), or stay a separate row?
3. Merge calls that differ only in volatile parameters (`workdir`, `max_output_tokens`, whitespace)? That is option 3 and needs per-agent logic.
4. Option 1 or option 2 (migration)? Decide before the release if option 2 is possible (schema timing above).
5. Do Types and Commands deserve their own group buttons, or should they be presets of the existing filters?
6. Naming: Sources vs Commands vs Tools; Invocations vs Commands + arguments.

## Depends on
Plans 4a and 4b (implemented). Interacts with Plan 7 (hints: repeated identical invocations are a prime "carried but dead" signal) and Plan 8 (similarity: invocations whose output differs slightly).
