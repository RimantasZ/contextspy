# Draft 4: Hot spots (per conversation / per session)

Status: DRAFT (not approved). Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [info-panel](../info-panel.md) (block occurrence identity + scope handling), [session-archive](../session-archive.md) (analysis must work without content).

## Goal (user's words, condensed)

Show the **top blocks of a session** by **occurrence count** or by **total token cost**, sortable by
either, so the user can see what information stays in the window and what it costs over time.

## Decisions carried over

- Cost = **tokens × occurrences** (footprint). No money, no cache weighting (D1, D2).
- Scope: **per conversation by default, switch to whole session** (D5), mirroring the dashboard;
  conversation membership from the existing lineage service. Lineage breaks are shown as events.
- Works on purged/archived content (D3): rows show type/tool/tokens, text only when retained.
- Aggregation in Python (policy); frontend only renders.

## Proposed design

- Reuse the block identity rule and the bulk-load query from plan 1; add a grouping function in
  `analysis/` (e.g. `block_hotspots.py`) producing rows:
  `identity (hash or tool_call_id), block_type, tool_name, source, label/preview, occurrence_count,
  tokens_per_occurrence, total_tokens, first_seen_seq, last_seen_seq, in_latest_request, request_count_in_scope`.
- Endpoint: `GET /api/sessions/{id}/hotspots?scope=conversation|session&conversation=C1&sort=total_tokens|occurrences&category=&limit=`.
- Filters: category, block type, tool, "still in latest request only".
- Optional grouping rows (open): file-content hot spots grouped by file path, tool results grouped by tool.
- UI placement (open): a section on `SessionDetail` and conversation view; clicking a row opens the
  existing info panel ("present in" list) and navigates to the requests. Must match Request-detail styling (D9).
- Rows expose the columns plan 7 needs (last seen, in-latest, tokens) so hints can be built on top.

## Open questions

1. Should hot spots group *identical content across different roles* (same text as tool result and
   user message) or follow the existing `content_hash` identity only? Default: existing identity.
2. File-path grouping: needs the file-content detection already in `classifier.py` (`_is_file_content`);
   confirm what metadata is stored on blocks (`attrs`).
3. Top-N default and pagination.
4. How to show a lineage break inside a conversation-scoped list (marker row vs. separate segments).

## Tests (sketch)

Counts and totals for repeated hashes; sort orders; scope filtering; in-latest flag; purged content;
archived session; conversation with a compaction break; query count bounded.
