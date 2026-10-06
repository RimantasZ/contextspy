# Idea 7: Optimisation hints ("carried but dead")

Status: IDEA only; not refined with the user. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [hot-spots](../hot-spots.md). Do not start before hot spots exist.

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
