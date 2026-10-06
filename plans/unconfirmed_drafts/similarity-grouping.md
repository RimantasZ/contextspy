# Idea 8 (v2): Similarity grouping — track blocks that changed and were reloaded

Status: **IDEA, postponed to v2 (user, 2026-10-06).** Not refined. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md). Extends [../hot-spots.md](../hot-spots.md) (Plan 4b), which groups blocks by **exact** content hash only.

## Why
Exact identity (`content_hash`) hides a pattern that matters for optimisation: a file is read, edited, and read again; or a tool definition / system prompt changes by a few characters; or a large tool result is regenerated with small differences. Each variant is a separate row in hot spots, so the user cannot see "this one thing was reloaded 7 times in slightly different forms and paid for each time". The user wants to **see cases where a file was changed and reloaded**, compared with today's behaviour (exact matches only).

## What it should answer
- Which logical items exist in several *versions* (same file, same tool definition, same prompt) and what do the versions cost in total?
- When did a version change, how big was the change (tokens added/removed, % of the block that differs), and was the old version still carried after the new one appeared?
- For files: "read at request #12 (4.1k tokens), edited at #15, re-read at #16 (4.3k tokens, 92% identical), the old copy stayed in context for 31 more requests".

## Ideas to evaluate (do not decide now)
1. **Version chains by identity key, not by text similarity.** With `blocks.file_path` (Plan 4a) the *Files* grouping already ties versions of one file together (`distinct_versions`). The v2 step would add the **timeline**: for one file, the ordered versions, per-version occurrence ranges, overlap (old and new carried simultaneously), and the diff size between consecutive versions. This needs no fuzzy matching and is probably the first thing to build.
2. **Diff between consecutive versions** reusing `analysis/context_diff.py` ideas and the text diff the compare feature (Plan 6) will need; only possible while content is retained (archive removes text), so persist a compact summary at capture or on first view: e.g. line counts and changed-line counts between a block and its previous version.
3. **Near-duplicate detection without a file path** (tool results, prompts, tool definitions, messages): content fingerprints such as SimHash/MinHash over token or line shingles stored per `block_contents` row (a few bytes each), compared within a scope using LSH buckets. Needs a similarity threshold (suggest ≥ 80-90%) and care with very large blocks.
4. **Persisting fingerprints survives archive** (the text does not), so similarity could still be computed for archived sessions; the cost is a derived column and a backfill that only works where text is retained.
5. **Presentation:** a "Versions" grouping next to Blocks / Sources / Files in hot spots, and a version-timeline view in the info panel ("changed N times; carried old + new together for M requests").

## Open questions
1. Is a file-path version chain (idea 1) enough for the first v2 step, or is similarity needed for non-file blocks right away?
2. Which similarity measure and threshold, and how to explain "92% identical" to a user in a way they trust.
3. Where fingerprints/diff summaries live (new column on `block_contents`? on `blocks`?), when they are computed (capture vs lazily) and how they are versioned/backfilled.
4. Cost control on very large sessions (8k+ distinct blocks): bucket by block type and size band first.

## Depends on
Plan 4a (`file_path`) and Plan 4b (hot spots) being implemented and used first; Plan 6 (compare) for the shared text-diff machinery.
