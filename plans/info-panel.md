# Plan 1: Info panel — shared pieces and "present in" / totals

Status: **implemented 2026-10-05 (uncommitted); not released; not checked in a browser.** Reviewed and decided before implementation; see "Implementation status" at the end for exactly what exists and how it differs from this text.

## Review 2026-10-05 (before implementation) — corrections applied below

Checked against the code after WI-0. Corrections made to this plan (details in the sections they touch):
1. **Ranges must be computed over the scope's own request order, not raw `session_seq`.** In conversation scope, requests of other conversations
   are interleaved in `session_seq` (C1 = 12, 13, 15, 16; 14 belongs to C2). A block present in all four C1 requests is *one* run, not two.
   Runs are over positions in the scope's ordered request list and are reported with both position and `session_seq` endpoints.
2. **Hash-less blocks have no cross-request identity** (the earlier `(block_type, tool_call_id)` grouping is dropped). Local servers
   (Ollama, llama.cpp, vLLM, OpenAI-compatible gateways) commonly reuse or omit call ids, so merging by id could join unrelated blocks.
   A hash-less block returns a single occurrence with `identity.kind = "none"`. (Duplicate-content tool results keep working: they have a hash.)
3. **Defined what "conversation scope" is**: the full `request_ids` of the conversation group the selected request is filed under
   (`_node_group_key`), including requests it shares with other conversations through shared history. Edge cases specified: auxiliary (`AUX`)
   requests and requests without a session (see Scope rules).
4. **"Latest request of scope"** is defined by the existing order `(session_seq, completed_at, id)` (`crud._sequence_order`).
5. **Navigating from the "present in" list needs the block id in the target request** (block ids are per-request rows) and a way to preselect it:
   the response includes `block_id` per listed request, and Request detail gains `?block=<id>` selection (it is local state today).
6. The plan's SQL strategy is specified (one query per session, filter by membership in Python) so conversation scope never builds an
   unbounded `IN (...)` list.

## Decisions taken in review (user, 2026-10-05)
1. **No shared-pieces extraction.** The two existing panels share little. The new section is a self-contained component inside the block inspector; extract later only if the tree page needs it. (Former deliverable 2's extraction bullet and deliverable 3 are dropped.)
2. **Add `?block=<id>` to Request detail** so "Present in" entries open the same block selected.
3. **Edge scopes fall back with a caption** (auxiliary → session scope; no session → the request alone), as specified under Scope rules.

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

### 1. Backend: block occurrences (no schema change; WI-0's v9 columns are enough — the `source_key` in the response is optional and NULL until WI-0 slice 3)

New pure function module `contextspy/analysis/block_occurrences.py` (aggregation logic belongs in
Python per `AGENTS.md`), fed by a `crud.py` query that bulk-loads the relevant `BlockRecord` rows.

Block identity (reuse the existing rule, do not invent a new one):
- Non-null `content_hash` → same block within the session (matches `crud.get_blocks`
  first-seen semantics). Occurrence-aware: every row counts, including repeats inside one request.
- Hash-less blocks (hidden/opaque reasoning, empty tool results, structural blocks) have **no cross-request identity**: the response contains
  just the selected block (`identity.kind = "none"`, one occurrence). Do not group by `tool_call_id`: ids are not reliable across providers.
- Input direction only for the first version (output blocks appear once).

Endpoint (additive):

```
GET /api/requests/{request_id}/blocks/{block_id}/occurrences?scope=conversation|session
```

Response (all computed server-side; **bounded: run-length ranges, never one row per occurrence** — a block can occur tens of thousands of times in a long session (32,254 in the author's sample), see `analysis-architecture.md` §1/§3):

```json
{
  "scope": "conversation",
  "scope_note": null,
  "identity": {"kind": "content_hash", "block_type": "tool_result", "tool_name": "Read", "source_key": "tool:Read", "activity": "read"},
  "ranges": [
    {"from_position": 3, "to_position": 31, "from_seq": 12, "to_seq": 40, "request_count": 29, "occurrence_count": 29},
    {"from_position": 35, "to_position": 35, "from_seq": 44, "to_seq": 44, "request_count": 1, "occurrence_count": 2}
  ],
  "requests_sample": [
    {"request_id": "...", "block_id": 981, "position": 3, "session_seq": 12, "conversation_code": "C1",
     "token_count": 1830, "context_fidelity": "complete", "is_current": false}
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

- **Positions, not sequence numbers, define runs.** `scope_requests` = the scope's requests ordered by `(session_seq, completed_at, id)`;
  `position` is the 0-based index in that list. A run is a maximal sequence of consecutive positions where the block occurs. `from_seq`/`to_seq`
  are the session sequence numbers of the run's end positions (for labels). A gap in a run means the block was really absent from a request of the scope.
- `requests_sample` holds at most ~50 entries: the first and last occurring requests, the current request (`is_current`), and the neighbours of each
  gap. Each carries the **`block_id` of that occurrence in that request** so the UI can open it. Expanding a run uses
  `GET .../occurrences/requests?from_position=&to_position=&limit=` (limit ≤ 200), returning the same entry shape.
- `tokens_per_occurrence` is the current block's `token_count`; if occurrences disagree, return `null` and rely on `total_visible_tokens` (the sum of every row's
  `token_count`). Totals are **visible tokens** (D11) and occurrence-aware.
- `scope_note`: null normally; otherwise a short machine-readable reason when the requested scope could not be honoured (see Scope rules).

Scope rules:
- `scope=session`: every request of the selected request's session, in session order.
- `scope=conversation` (default): the `request_ids` of the conversation group the selected request is filed under
  (`crud.get_session_lineage_graph` → `_node_group_key` / `_conversation_code_resolver`, the same helpers behind the `C<n>` codes). A request
  in several conversations is filed under its confirmed one first, then its first membership (existing rule). Requests it shares with other
  conversations through shared history **are** members of the scope. **Reuse those helpers; do not re-implement membership.** Order
  `external` graph nodes out (they are not in this session).
- **Auxiliary (`AUX`) requests** have no conversation: `scope=conversation` falls back to `session` with `scope_note = "auxiliary_request"`.
- **Requests without a session** (`session_id` NULL): both scopes return just that request (`scope_request_count = 1`, `scope_note = "no_session"`).
- Computing: one SQL query for the session (`blocks` joined to `requests` by `content_hash`, `direction='input'`, the session id) returns
  `(request_id, block_id, token_count)` rows; membership filtering and run-building happen in Python (no large `IN (...)` lists).

Notes:
- Per-occurrence `token_count` is taken from each row (it can differ from the first occurrence, e.g. system prompts
  with a volatile header are counted on the full text sent).
- `scope_request_count` lets the UI say "present in 9 of 20 requests".
- Must be a bounded, single bulk query per call (no N+1) and work when content is purged (D3).
- 404 for unknown request/block, or a block that does not belong to the request; blocks never leak across sessions. Output-direction blocks: single occurrence.
- Performance reference (author's sample, SQLite): all occurrences of the most repeated block (32k rows) ≈ 0.35 s; the conversation graph is already cached
  per revision. No new cache or index is planned; measure before adding one.

### 2. Frontend

- *(Dropped in review: no extraction of shared pieces from `BlockInspector`/`ContextChangePanel`.)*
- Add a **"Present in"** section to `BlockInspector`:
  - summary line: `9 of 20 requests · 1,830 tokens each · 16,470 total` (formatted from API values),
  - first seen / last seen / still in latest request,
  - scope toggle `This conversation | Whole session` (default conversation),
  - the runs as chips (`#12–#40 · 29 requests`, `#44`), the current request highlighted; expanding a run lists its requests (lazy,
    via the expansion endpoint). Each listed request navigates to **Request detail with that occurrence selected**: `/requests/{id}?block={block_id}`.
    **Request detail must learn `?block=`**: today block selection is local state in `RequestWorkbench` (`select()`); initialise it from the
    query parameter once the blocks load and keep it in sync (replace, not push, history entries). Unknown/missing ids are ignored.
- Purged-content blocks still show the section (D3).
- API types in `ui/src/api/client.ts`, hooks in `ui/src/api/hooks.ts` (`useBlockOccurrences(requestId, blockId, scope)`, enabled only when a block is selected; keep the
  previous result while the scope toggles). `BlockInspector` currently receives only `block`/`blocks`: add the owning `requestId` prop.
- Show `scope_note` as a short caption ("Auxiliary request: showing the whole session", "Request has no session").
- Make the section a self-contained component so plan 5 can mount it in the tree page pane.

### 3. Request-level facts in the panel

*(Dropped in review. `ContextChangePanel` is left unchanged.)*

## Tests

Backend (`tests/`, new `test_block_occurrences.py` or in `test_dashboard_stats.py` style):
1. Same hash in N requests → N occurrences, correct totals/first/last.
2. Conversation scope excludes requests from other conversations of the same session; session scope includes them.
2b. **Interleaved conversations**: C1 = seq 12, 13, 15, 16 with seq 14 in C2; a block in all C1 requests is one run (positions 0–3), and in session scope the same block is two runs split at 14.
2c. A block missing from the middle of a conversation produces two runs with the gap at the right position; within-request repeats raise `occurrence_count` but not `request_count`.
3. Hash-less blocks (with or without `tool_call_id`) return a single occurrence and never merge across requests, even when two requests reuse the same call id.
3b. Auxiliary request falls back to session scope with `scope_note`; request without a session returns only itself with `scope_note`; a request shared by two conversations belongs to both scopes.
3c. `requests_sample` is capped, includes first/last/current/gap neighbours, and every entry's `block_id` belongs to that entry's request.
4. Same hash in a different session is ignored.
5. Purged content still returns occurrences.
6. `in_latest_request_of_scope` true/false cases; a block dropped by compaction (lineage break).
7. Unknown request/block → 404; block not belonging to the request → 404.
8. Query-count test: constant number of queries regardless of request count.

Frontend (Vitest/RTL, next to `BlockInspector.test.tsx`): section renders summary from API, scope
toggle refetches and keeps the previous data while loading, run expansion, navigation to `?block=`, `scope_note` captions, loading/error/purged states.
`RequestWorkbench`/`RequestDetail`: `?block=` preselects the block after load, ignores unknown ids, and clearing the selection removes the parameter.

## Not in scope

- Cache read/write reporting, dollar cost (D1, D2).
- Hot spots aggregation (plan 4), compare action (plan 6), tree page (plan 5).
- Output-block occurrences.

## Open questions

- Should the occurrences list also mark requests where the block was *absent but expected* (dropped by
  compaction)? Probably a plan-4/7 concern; not needed here.

## Implementation status (2026-10-05)

**Implemented** (backend 493 / frontend 163 tests passing, `npm run check` and `make ui` clean; uncommitted):
- `analysis/block_occurrences.py` (pure runs/totals/sample/expansion) and `db/block_occurrence_service.py` (scope selection, one query per call, membership cache).
- `GET /api/requests/{id}/blocks/{block_id}/occurrences?scope=conversation|session` and `.../occurrences/requests?scope=&from_position=&to_position=&limit=`
  (limit default 100, max 200; 404 for unknown request/block or a block of another request; 422 for a bad scope).
- UI: `BlockOccurrences` ("Present in") inside `BlockInspector` for input blocks of a known request; `?block=<id>` in Request detail
  (initial selection, mirrored into the URL with `replace`); entries open `/requests/{id}?block={block_id}`.
- Docs: `SPEC.md` (API table), `docs/changelog.md`.

**Differences from the text above**
- Response also carries `requested_scope` (the scope asked for); `scope` is the one used. `scope_note` values: `no_session`, `auxiliary_request`, `conversation_unavailable`.
- Entries' `conversation_code` is the code of the conversation *that request* is filed under (same as its dashboard card). A shared-history request
  (e.g. a fork's root) can therefore show a different code than the rest of the scope.
- **Conversation membership is cached for 60 s per session** (`MEMBERSHIP_TTL_SECONDS`), invalidated immediately when the session's request count changes.
  Reason, measured on the author's database (sample only): `get_session_lineage_graph` costs 1.2–1.5 s per call even when cached (the revision check hashes every
  block row; 3.7–6.7 s on a 4,032-request session) and 2.5–8 s cold (14 s / 58 s when the graph had not been built in the process). With the cache the second selection
  within a minute is 0.2–0.4 s. In-place lineage edits can therefore be up to a minute stale here; new requests never are. Session scope never touches the graph (0.14–2 s).
  **Consequence:** the first conversation-scope selection per session per minute can take seconds on long sessions; the UI shows "Loading…".
- The expansion endpoint is `.../occurrences/requests`, and runs/positions are 0-based indexes into the scope's request order.
- `tokens_per_occurrence` is the single `token_count` when all occurrences agree, otherwise `null`.
- The UI hides the section for output blocks and when `BlockInspector` has no `requestId`. Entries are not clickable when no `onOpenOccurrence` is supplied (tests).
- Test-environment change: `setupTests.ts` stubs `Element.prototype.scrollIntoView` (jsdom lacks it; selecting a block now scrolls to it in tests).

**Not done / open**
- No cache or index was added for the SQL side; session scope on the largest sample session was 0.14 s warm / 2 s cold.
- The conversation panel (`ContextChangePanel`) is unchanged by design (review decision 1).
- Not looked at in a real browser; no screenshots. Visual match with the rest of Request detail is unverified.
- Block ids in the URL are request-local row ids: a `?block=` link stops resolving if the request's blocks are rebuilt by a data migration.

