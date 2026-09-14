# Session analysis page: request tree and block-to-JSON navigation

## Status and dependency

This plan is the second of two related implementation plans. It assumes
`JSON_RECONSTRUCTION_PLAN.md` has already been implemented and verified.

In particular, this plan assumes:

- Requests and responses have canonical JSON documents independent of transport.
- Streamed response reconstruction occurs before block parsing.
- The formatted JSON view and block analysis consume the same canonical JSON.
- Complete normalized stream events remain available separately when needed.
- Capture/purge/reconstruction states are explicit in the API.

The session-analysis work may be clarified after the JSON reconstruction refactor. The initial
scope below focuses on input/context blocks and request JSON, because the requested tree describes
blocks sent from the agent to the LLM.

## User outcome

Add a dedicated session-analysis screen with:

- A request/block tree on the left.
- Request nodes as the first visible level, ordered by session request number.
- Blocks first introduced in each request as child nodes.
- A content panel on the right.
- An isolated block-content mode with formatting and optional token highlighting.
- A request-JSON mode showing the complete formatted and syntax-highlighted canonical request.
- Selecting a block in request-JSON mode expands and scrolls to the exact JSON node from which the
  block was derived.

## Default tree semantics

The default tree groups a block under the request in which its content first appeared:

```text
Request #1
  System prompt
  Tool: read_file
  User message
Request #2
  Tool result: read_file
  User message
Request #3
  User message
```

This uses the existing `first_seen_session_seq` concept and prevents repeated context from
appearing under every request. Requests with no newly introduced blocks remain visible.

A later optional toggle may offer:

- `Introduced`: only blocks first seen in that request (default).
- `Full context`: every input block present in that request.

The backend, not the frontend, must define and calculate these scopes.

## Existing groundwork

- `Request.session_seq` supplies the session-local request number.
- `BlockRecord` persists block type, position, message index, category, content hash, token count,
  tool metadata, and attrs.
- `BlockContent` deduplicates content by hash.
- `crud.get_blocks()` already derives `first_seen_session_seq` within a session.
- `ParsedViewer` already contains block labeling, category colors, token highlighting, and block
  content states.
- `RawViewer` already contains a recursive syntax-colored JSON renderer.
- `SessionDetail` already lists session requests and is the natural entry point.

## Main gaps

1. Blocks do not record where they originated in canonical request JSON.
2. `get_blocks()` calculates first-seen information for one request at a time; fetching an entire
   session through existing endpoints would create N+1 API/DB work.
3. The session request list is capped at 500 and is not an analysis-specific API.
4. `RawViewer.JsonNode` owns collapse state recursively, so a parent cannot programmatically
   expand ancestors and scroll to a selected JSON path.
5. Block presentation helpers and token colors are private to existing components.
6. `ParsedViewer` eagerly tokenizes all blocks, which is unsuitable for session-scale navigation.
7. Token highlighting truncation is currently silent.

## Block-to-JSON source location

### Data model

Add a typed canonical JSON path to every block produced from request or response JSON:

```python
json_path: tuple[str | int, ...] | None
```

API representation:

```json
["messages", 3, "content", 1, "text"]
```

Use typed path segments rather than raw character offsets or substring matching:

- Formatting does not invalidate the location.
- Repeated identical strings are unambiguous.
- Array indexes remain explicit.
- Tool definitions/arguments serialized differently by the adapter still map correctly.
- The UI can expand every ancestor deterministically.

For a block assembled from multiple JSON leaves, store the smallest enclosing container path. If
future UX requires highlighting several disjoint nodes, evolve this to `json_paths`, but do not
start with ambiguous content searching.

### Capture-time population

Populate `json_path` while adapters traverse canonical JSON. Examples:

- Anthropic string system prompt: `["system"]`.
- Anthropic system part: `["system", 0, "text"]`.
- Tool definition: `["tools", 2]`.
- Message string content: `["messages", 4, "content"]`.
- Content text part: `["messages", 4, "content", 1, "text"]`.
- Tool-use input: `["messages", 5, "content", 0, "input"]`.
- OpenAI tool-call arguments: `["messages", 5, "tool_calls", 0, "function", "arguments"]`.
- Responses API function-call arguments: `["input", 3, "arguments"]`.

Because the JSON reconstruction plan routes every transport through canonical JSON before block
analysis, output blocks may use the same mechanism against canonical response JSON. The initial
session page only needs input-block paths, but implement the field generically.

### Persistence and migration

Add `json_path` as JSON text on `BlockRecord` and expose it as a decoded array through the API.
Update:

- `contextspy/analysis/blocks.py`
- Every module in `contextspy/analysis/adapters/`
- `contextspy/db/models.py`
- `contextspy/db/crud.py`
- `contextspy/db/database.py` additive migrations
- `ui/src/api/client.ts`

The JSON reconstruction plan should already have generalized additive migrations to accept a
table name. Use that mechanism for `blocks.json_path`.

Add a versioned data migration only if historical-path backfill is required. It should:

1. Select requests whose canonical request/response bodies are still retained.
2. Reparse them through the canonical adapter pipeline.
3. Match reconstructed blocks to existing records by direction and position, with defensive
   checks for type/content hash.
4. Update `json_path` when the match is unambiguous.
5. Leave it null when canonical JSON has been purged or historical analysis no longer matches.

Never infer a path through content substring search during migration.

## First-seen identity

Initially preserve the repository's existing identity rule: equal non-null `content_hash` means
the same content block within a session. The earliest request containing that hash determines
`first_seen_session_seq`.

Consequences to document:

- Identical text used in different semantic roles shares the same first-seen request.
- Content-less structural/hidden blocks cannot be deduplicated by hash and remain attached to
  their occurrence request.
- Duplicate occurrences in the first request may remain distinct tree children because they are
  distinct block records.

If role-sensitive identity becomes necessary, change it deliberately to a documented composite
such as `(direction, block_type, content_hash, tool_name)` and update all existing first-seen
behavior/tests together. Do not silently use a different identity only on the new page.

## Session analysis API

Add:

```text
GET /api/sessions/{session_id}/analysis?scope=introduced
```

Suggested response:

```json
{
  "session": {
    "id": "...",
    "name": "...",
    "is_active": false
  },
  "scope": "introduced",
  "requests": [
    {
      "id": "...",
      "session_seq": 1,
      "timestamp": "...",
      "provider": "anthropic",
      "model": "...",
      "agent": "...",
      "tokens_total_input": 1234,
      "canonical_request_available": true,
      "blocks": [
        {
          "id": 42,
          "direction": "input",
          "position": 0,
          "message_index": -1,
          "block_type": "system_prompt",
          "category": "system_prompt",
          "content": "...",
          "content_purged": false,
          "token_count": 200,
          "tool_name": null,
          "tool_call_id": null,
          "attrs": {},
          "json_path": ["system"],
          "first_seen_session_seq": 1
        }
      ]
    }
  ]
}
```

Do not include every canonical request body in this response; that would make initial page load
scale with the sum of all repeated context. Load the selected request body lazily through the
existing request-detail endpoint or a focused canonical-body endpoint.

### Query behavior

Implement a dedicated CRUD query that:

1. Validates the session exists.
2. Loads all session requests ordered by `(session_seq, timestamp, id)` without the existing
   500-row list limit.
3. Loads all input block records and retained block contents for those request IDs in one bulk
   query.
4. Calculates first-seen sequence once for the whole session.
5. Groups blocks under requests in Python/backend code.
6. Applies `introduced` or future `all` scope in the backend.

Avoid calling `crud.get_blocks()` once per request. Analysis/grouping logic belongs in Python per
the repository policy; the frontend should only render the returned structure.

### Payload-size considerations

Introduced scope sends each unique retained content value approximately once, which should remain
far smaller than sending every request's full repeated context. If real sessions still produce
large responses, add block-detail lazy loading or cursor pagination without changing the logical
tree contract.

Return metadata for purged content rather than omitting the block.

## Routing and entry point

Add a route:

```text
/sessions/:id/analysis
```

Register it in `ui/src/App.tsx`. Add an `Analyze context` button to the session header in
`ui/src/pages/SessionDetail.tsx`. A global navigation item is not required because analysis is
scoped to a selected session.

Add:

- `sessionsApi.analysis(sessionId, scope)` in `ui/src/api/client.ts`.
- `useSessionAnalysis(sessionId, scope)` in `ui/src/api/hooks.ts`.
- Query invalidation for session-analysis data on `new_request` WebSocket events.

For active sessions, retain the current selection while newly introduced request nodes appear.

## Page layout

Create `ui/src/pages/SessionAnalysis.tsx` with a full-height two-pane layout inside the existing
application shell:

```text
┌──────────────────────────────┬──────────────────────────────────────────────┐
│ Session / request tree       │ Selected block                              │
│                              │                                              │
│ ▼ Request #1                 │ [Isolated block] [Request JSON]              │
│     System prompt            │                                              │
│     Tool: read_file          │ formatted / highlighted content              │
│     User message             │                                              │
│ ▶ Request #2                 │                                              │
└──────────────────────────────┴──────────────────────────────────────────────┘
```

- Left pane: approximately 320–400 px, independently scrollable, with a reasonable minimum.
- Right pane: consumes remaining width and manages its own content scrolling.
- On small screens: stack or use a tree drawer; desktop behavior is the first implementation
  priority.
- Keep the session name, back navigation, active/ended status, and scope selector in a compact
  page header.

## Tree behavior

Create `SessionRequestTree.tsx`:

- Request nodes display request number, model/provider, time, input tokens, and count of introduced
  blocks.
- Block nodes use consistent labels/colors extracted from `ParsedViewer`.
- Selected request and selected block have distinct visual states.
- Clicking a request expands/collapses it; clicking a block selects it without collapsing the
  parent.
- Auto-expand the first request with blocks on initial load.
- Preserve expansion state during live-query refreshes.
- Requests with no introduced blocks show `No new blocks` rather than disappearing.
- Purged content/path availability is indicated without preventing selection.
- Use semantic buttons/tree roles and keyboard-accessible focus behavior.

Reflect selection in URL search parameters where practical:

```text
?request=<request-id>&block=<block-id>&view=isolated
```

This supports refresh/back navigation and shareable local links.

## Shared block presentation

Extract reusable block presentation concerns from `ParsedViewer.tsx`:

- `blockLabel()`.
- Structural visual/category mapping.
- Bar/text/background/border colors.
- Token color palette.
- Common metadata chips.

Place them in focused modules/components rather than importing one page-sized viewer from another,
for example:

- `ui/src/components/blocks/blockPresentation.ts`
- `ui/src/components/blocks/TokenizedContent.tsx`
- `ui/src/components/blocks/BlockMetadata.tsx`

Update existing viewers to use the extracted helpers so block labels and colors remain consistent.

## Isolated block mode

Create `BlockContentPane.tsx` with:

- Block type/category/tool/message metadata.
- `First seen in request #N`.
- Captured block token count.
- Retention/purge state.
- Plain text with preserved whitespace when content is not JSON.
- Parsed, indented, syntax-colored rendering when block content is valid JSON.
- `Highlight tokens` toggle.

Tokenization must be lazy and limited to the selected block. Cache results by content hash or block
ID through React Query or local component state. Do not eagerly send every session block to
`/tokenize`.

Extend the tokenizer API response to include truncation metadata, for example:

```json
{
  "tokens": ["..."],
  "truncated": true,
  "displayed_token_count": 8000
}
```

When pretty formatting valid JSON, token highlighting may tokenize the displayed pretty-printed
representation. Keep the captured block token count clearly separate because added formatting
whitespace can change displayed token boundaries/count.

## Request JSON mode

Create a reusable controlled `JsonDocumentViewer.tsx` rather than extending the current recursive
component with more local state.

Requirements:

- Parse and format the complete canonical request JSON.
- Syntax-color object keys, strings, numbers, booleans, and null.
- Track every node by typed JSON path.
- Keep collapsed/expanded paths in parent-controlled state.
- Expose `revealPath(path)` or equivalent behavior.
- Expand every ancestor of the selected block path.
- Scroll the exact node into view after rendering.
- Apply a temporary and persistent selected-node highlight.
- Preserve a user's unrelated collapse choices when revealing a path.
- Support search without confusing search highlighting with selected-block highlighting.

Selection flow:

1. User selects a block in the tree.
2. User opens `Request JSON`, or the existing JSON tab remains active.
3. Fetch the canonical request body lazily if it is not cached.
4. Validate/traverse `block.json_path` against the parsed document.
5. Expand its ancestors.
6. After React commits the expanded tree, scroll the target node to the center.
7. Highlight the target until another block is selected.

Do not fall back to substring search when a path is absent or invalid. Show a clear state:

- `Location unavailable for this legacy capture.`
- `Canonical request was purged by retention.`
- `Stored path no longer resolves; capture may predate this adapter version.`

## Canonical JSON integration

The page must use the canonical request JSON established by `JSON_RECONSTRUCTION_PLAN.md`; it must
not independently parse transport events or reconstruct provider payloads in TypeScript.

For a future output/session-response mode, use canonical response JSON and output-block
`json_path` in exactly the same viewer. Normalized transport events should remain a separate
Events view because their paths are not paths into canonical provider response JSON.

## Retention and failure states

Handle these states independently:

- Block metadata exists, content retained, canonical request retained: both modes work.
- Block content purged, canonical request retained: isolated mode reports purge; JSON mode can
  still reveal the source node.
- Block content retained, canonical request purged: isolated mode works; JSON mode reports purge.
- Both purged: metadata/token count remains visible.
- `json_path` missing: isolated mode works; JSON mode reports unavailable location.
- Canonical request invalid/unparseable: show complete text fallback, but disable path reveal.
- Capture/reconstruction failed: distinguish from retention using metadata introduced by the JSON
  refactor.

## Backend tests

1. Adapter fixtures assert exact `json_path` values for every request block type/provider.
2. Session analysis groups repeated hashes under their earliest session request.
3. Identical content in another session does not affect first-seen values.
4. Blocks with no content hash remain represented.
5. Duplicate occurrences in the first request remain stable and ordered.
6. Requests with no introduced blocks remain in the response.
7. Ordering is deterministic when timestamps or session sequences collide.
8. Sessions with more than 500 requests are complete.
9. Purged block content and purged canonical request bodies return distinct states.
10. A missing or invalid JSON path remains nullable and never triggers content search.
11. Historical path backfill updates only unambiguous matches.
12. The endpoint returns 404 for a missing session and does not leak blocks across sessions.

## Frontend tests

The UI currently has no dedicated test framework. Add Vitest and React Testing Library if this
feature is implemented with automated component coverage; otherwise keep components sufficiently
factored to add them immediately afterward.

Cover:

- Request and introduced-block rendering.
- Expand/collapse and keyboard selection.
- URL-backed selection restoration.
- Lazy request-body fetch.
- JSON ancestor expansion and exact target scrolling.
- Highlighting paths containing both object keys and array indexes.
- Switching between isolated and JSON modes without losing selection.
- Lazy tokenization and truncation messaging.
- Live-session refresh without collapsing the tree.
- All retention, missing-path, and invalid-JSON states.

## Implementation sequence

1. Confirm the canonical JSON refactor completion criteria are met.
2. Add `Block.json_path` and populate request/output paths in every adapter.
3. Persist/expose `json_path` and add migrations/backfill if required.
4. Add the bulk session-analysis CRUD query and API endpoint.
5. Add frontend API types/hooks and WebSocket invalidation.
6. Extract shared block presentation/token content components.
7. Extract/build controlled `JsonDocumentViewer` and update existing raw viewers to use it where
   appropriate.
8. Build `SessionRequestTree` and isolated block pane.
9. Build `SessionAnalysis` page, routing, URL selection, and Session Detail entry point.
10. Add retention/error/legacy states and responsive behavior.
11. Add backend/frontend tests and run full verification.
12. Update `SPEC.md`, `docs/development.md`, screenshots/user docs, and changelog.

## Verification commands

```bash
pytest
cd ui && npm run build
```

Manual verification should use at least:

- One Anthropic Messages session.
- One OpenAI Chat Completions session with tool calls.
- One OpenAI Responses session transported over SSE or WebSocket.
- One Ollama session.
- One active session receiving new requests while the page is open.
- One session whose raw bodies or block contents have been purged.

For each, verify that introduced blocks appear under the correct request, isolated content is
accurate, and `Request JSON` reveals the exact canonical node that produced the block.

## Completion criteria

The session-analysis feature is complete when:

- A dedicated session route renders all session requests without the existing 500-row limitation.
- Introduced blocks are grouped by backend-calculated first-seen request.
- Selecting a block shows formatted isolated content with optional token highlighting.
- The canonical request JSON view expands, scrolls to, and highlights the exact `json_path` node.
- No frontend code reimplements block analysis or first-seen logic.
- Active-session updates, retention, legacy, and failure states behave predictably.
- Backend tests pass and the production UI builds successfully.

