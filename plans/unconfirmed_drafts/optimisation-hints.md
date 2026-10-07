# Idea 7: Optimisation hints ("carried but dead")

Status: IDEA only; not refined with the user; kept as an idea until after the first release (roadmap D23). Updated 2026-10-06 with "State of the code" below. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [hot-spots](../archive/hot-spots.md) (**implemented**; its rows are the input).

## Idea

The most useful optimisation signal is **context that is carried but no longer used**: e.g. a
12k-token file read never referenced again, tool results from 30 turns ago, tool definitions for
tools never called. Surface these as hints on top of hot spots.

## Inputs already planned in hot spots

`total_tokens`, `occurrence_count`, `first_seen_seq`, `last_seen_seq`, `in_latest_request`.

## Open product questions

- What is "referenced"/"used"? Cheap proxies: the block's tool call is followed by assistant actions
  touching the same file/tool; the tool was ever called in the conversation; the content hash shows
  up in later *output* text (weak). Needs user input and experimentation on real sessions.
- Thresholds (size × age) for "dead weight"; per-agent differences (some agents re-read files).
- Tokens-only (D1): hints must be phrased in tokens ("~12,000 tokens carried for 30 requests").
- Keep to analysis in Python (policy); hints are labelled estimates, never prescriptive.

## State of the code (2026-10-06)

**Inputs that now exist** (hot-spots API `GET /api/sessions/{id}/hotspots`, per block row): `total_tokens`, `occurrence_count`, `request_count`, `tokens_per_occurrence`, `first_seen_session_seq`, `last_seen_session_seq`, `in_latest_request`, `run_count` (more than one = left the context and came back), `latest` pointer, `source_key`, `activity`, `file_path`, `category`, `block_type`. The `in_context=dropped` filter already returns blocks whose last occurrence is before the scope's latest request. Source rows carry `largest` (the biggest block) and file rows split `result_tokens` (reads) from `call_tokens` (edits, including patch text, which is carried too).

**Cheap "used" proxies now possible** (all from stored structure, so they work after archive): a later `tool_call` with the same `file_path` after a read; whether any call exists for a tool whose definition is carried (`source_key` `tool:<name>` for definitions vs calls); `purpose_detail.trailing_tool_results` of later requests; whether a block was `dropped` (cost already paid) or still carried.

**Candidate hints** (to be validated with the user; phrase in tokens, D1; label as estimates): large block carried N requests after its last related call; tool definitions of tools never called in the conversation (cost = tokens x requests); the same file read repeatedly with identical content (`distinct_versions` 1, high `occurrence_count`); a large result still in context after a `compaction` request would have dropped it; patch text carried long after the edit.

**Caveats found while building 4a/4b:** path coverage is partial (8% of blocks in the author's sample) and a file's relative and absolute spellings are separate rows; where call text was purged before source keys were derived, Codex calls are only `tool:exec`/`tool:js`; opaque/partial requests hide part of the window, so hints describe *visible* tokens only; there is no cache yet for hot-spots aggregation (issue #67), so hints should reuse one aggregation per request and not call it repeatedly; the cold conversation-membership cost applies to conversation scope.

