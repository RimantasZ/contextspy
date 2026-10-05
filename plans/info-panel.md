# Plan 1: Info panel — shared pieces and "present in" / totals

Status: confirmed, not started. Part of [ANALYSIS_ROADMAP.md](ANALYSIS_ROADMAP.md) (read it for decisions D1–D10).

## Goal

Answer, for any block the user selects: **in which requests is this block present, and what is its
total token cost across them?** Make this (and later actions such as compare) reachable from the
places that already have an info panel, and reusable as the right-hand pane of the future context
tree page.

Dependency: shows `source_key`/`activity` from [wi0-data-foundation.md](wi0-data-foundation.md) when present (works without them).

## Scope decision (agreed with user)

- **No new panel framework and no drawer variant.** Two panels exist today:
  - `ui/src/components/request/BlockInspector.tsx` — block panel in Request detail.
  - `ui/src/components/dashboard/ContextChangePanel.tsx` — request-level panel in the conversation view.
  These are sufficient. The tree page (plan 5) will embed the same panel as its right pane.
- Cost is **tokens only** (D1). No cache weighting (D2); cache fields stay where they are.
- Scope switch: **conversation (default) / whole session** (D5).

## Deliverables

### 1. Backend: block occurrences (no schema change)

New pure function module `contextspy/analysis/block_occurrences.py` (aggregation logic belongs in
Python per `AGENTS.md`), fed by a `crud.py` query that bulk-loads the relevant `BlockRecord` rows.

Block identity (reuse the existing rule, do not invent a new one):
- Non-null `content_hash` → same block within the session (matches `crud.get_blocks`
  first-seen semantics).
- Hash-less blocks (hidden/structural) → identified by `(block_type, tool_call_id)` when a
  `tool_call_id` exists, otherwise they have no cross-request identity and return a single occurrence.
- Input direction only for the first version (output blocks appear once).

Endpoint (additive):

```
GET /api/requests/{request_id}/blocks/{block_id}/occurrences?scope=conversation|session
```

Response (all computed server-side; **bounded: run-length ranges, never one row per occurrence** — a block can occur tens of thousands of times in a long session (32,254 in the author's sample), see `analysis-architecture.md` §1/§3):

```json
{
  "scope": "conversation",
  "identity": {"kind": "content_hash", "block_type": "tool_result", "tool_name": "Read", "source_key": "builtin:Read"},
  "ranges": [
    {"from_seq": 12, "to_seq": 40, "occurrence_count": 29, "request_count": 29},
    {"from_seq": 44, "to_seq": 44, "occurrence_count": 2, "request_count": 1}
  ],
  "requests_sample": [
    {"request_id": "...", "session_seq": 12, "conversation_code": "C1", "token_count": 1830, "context_fidelity": "complete"}
  ],
  "totals": {
    "occurrence_count": 31,
    "request_count": 30,
    "tokens_per_occurrence": 1830,
    "total_visible_tokens": 56730,
    "first_seen_session_seq": 12,
    "last_seen_session_seq": 44,
    "in_latest_request_of_scope": true,
    "scope_request_count": 60,
    "fidelity_counts": {"complete": 20, "partial": 4, "opaque": 6}
  }
}
```

- `ranges` are contiguous runs of `session_seq` where the block is present (gaps = absent). `requests_sample`
  holds at most ~50 requests (first, last, and the current request); the rest is fetched with
  `GET .../occurrences?from_seq=&to_seq=&limit=&cursor=` when the user expands a range.
- Totals are **visible tokens** (D11) and occurrence-aware (a block repeated within one request counts each time).

Notes:
- `scope=conversation` uses the conversation membership already computed for the dashboard
  (`session_lineage_service.graph_for_session`, `crud._node_group_key`, `annotated_lineage_nodes`).
  A request in several conversations: use the same "confirmed first, then first membership" rule as
  `annotated_lineage_nodes`. **Reuse those helpers; do not re-implement membership.**
- Per-occurrence `token_count` is taken from each row (it can differ from the first occurrence, e.g. system prompts
  with a volatile header are counted on the full text sent).
- `scope_request_count` lets the UI say "present in 9 of 20 requests".
- Must be a bounded, single bulk query per call (no N+1) and work when content is purged (D3).
- 404 for unknown request/block; blocks never leak across sessions.

### 2. Frontend

- Extract the pieces `BlockInspector` and `ContextChangePanel` share (metadata grid rows, section
  chrome) into small components under `ui/src/components/request/` (or a new `components/panel/`),
  only where genuine duplication exists. Verify first by reading both files; `BlockInspector.tsx`
  was only partially reviewed when this plan was written.
- Add a **"Present in"** section to `BlockInspector`:
  - summary line: `9 of 20 requests · 1,830 tokens each · 16,470 total` (formatted from API values),
  - first seen / last seen / still in latest request,
  - scope toggle `This conversation | Whole session` (default conversation),
  - a compact list of requests (`#12 C1 … #31 C1`), each navigating to that request detail,
    current request highlighted. Long lists: collapse to first/last N with "show all".
- Purged-content blocks still show the section (D3).
- API types in `ui/src/api/client.ts`, hook in `ui/src/api/hooks.ts`; fetch lazily when a block is selected.
- Make the section a self-contained component so plan 5 can mount it in the tree page pane.

### 3. Request-level facts in the panel (small, optional in this plan)

If cheap after reading `ContextChangePanel`: ensure it shows the request's token total and delta
versus its baseline (already largely there). Cache fields are **not** added here (D2).

## Tests

Backend (`tests/`, new `test_block_occurrences.py` or in `test_dashboard_stats.py` style):
1. Same hash in N requests → N occurrences, correct totals/first/last.
2. Conversation scope excludes requests from other conversations of the same session; session scope includes them.
3. Hash-less block with `tool_call_id` groups across requests; hash-less without id → single occurrence.
4. Same hash in a different session is ignored.
5. Purged content still returns occurrences.
6. `in_latest_request_of_scope` true/false cases; a block dropped by compaction (lineage break).
7. Unknown request/block → 404; block not belonging to the request → 404.
8. Query-count test: constant number of queries regardless of request count.

Frontend (Vitest/RTL, next to `BlockInspector.test.tsx`): section renders summary from API, scope
toggle refetches, list navigation, loading/error/purged states.

## Not in scope

- Cache read/write reporting, dollar cost (D1, D2).
- Hot spots aggregation (plan 4), compare action (plan 6), tree page (plan 5).
- Output-block occurrences.

## Open questions

- Should the occurrences list also mark requests where the block was *absent but expected* (dropped by
  compaction)? Probably a plan-4/7 concern; not needed here.
