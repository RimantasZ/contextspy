# Draft 5: Context tree page

Status: DRAFT (not approved; kept as a draft on purpose until after the first release of the analysis work, see roadmap D23). Updated 2026-10-06 with "State of the code" below; the open questions at the end are still unanswered. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [info-panel](../info-panel.md) (implemented), [request-purpose](../request-purpose.md) (baseline implemented; **turn grouping is not**), and the helpers of [file-paths](../file-paths.md) / [hot-spots](../hot-spots.md) (implemented).
Replaces the deleted `SESSION_ANALYSIS_PLAN.md` (see roadmap D6 for what was kept).

## Goal

A page that shows a request (and, within a conversation, how requests relate) as a **hierarchical
tree**, like a project/file explorer. Tree on the left, **info panel on the right** that reacts to the
selected node. The right pane shows block details, block content (same formatter viewers as Request
detail), or the **raw request JSON** with scroll to the block's actual location (traceability: where
does this information actually come from).

## Visual requirements (D9 — important, earlier attempts looked bad)

- Page style must match Request detail (`pages/RequestDetail.tsx`, `.page-shell`, `.panel`,
  `.surface`, `--border`/`--text-muted` tokens). Before building, write down the tokens/spacing used
  there and reuse them.
- Focus on: tree representation, node icons, **smooth collapse/expand animation** (height ease +
  chevron rotation), **lightweight minimal node labels**: chevron · icon · short name · muted right-aligned token count. Everything else goes in the info panel.
- Subtree token totals shown on groups (at least when collapsed); thin token-share bar optional.
- Keyboard navigation (arrows, left/right collapse/expand, Enter select), tree ARIA roles.
- Build a **generic `<Tree>` component first** (virtualised for large requests), then the block tree on top.
- Icons from one set mapped by block type/purpose/source (`lib/blockVisuals.ts` is the existing
  mapping); MCP/subagent variants = base icon + small badge, not new icons.

## Grouping modes (backend computes the structure; frontend only renders)

1. **By introduction** (default): request → blocks first added in it, relative to the lineage parent
   (reuse `context_diff` / the `show-new-block-only` baseline rule: parent edge, else previous request
   in conversation).
2. **By turn**: user message → tool call → result → tool call → result → … → final assistant text,
   using derived turn grouping from plan 2.
3. **By tool/category**: tool definition ↔ call ↔ result linked by `tool_call_id`
   (`linked_call_id`, `linked_definition_id` already exist on block payloads).

Scope: per conversation by default, switch to whole session (D5). Lineage breaks appear as nodes.

## Info panel (right pane)

Tabs: **Details** (type, category, source, position, tokens, "present in" from plan 1), **Content**
(existing formatters), **Raw JSON** (pretty JSON with reveal/scroll to the block's node).
Actions (compare with…, open request, etc.) eventually live in the panel (D8).

## Raw-JSON traceability (carry-over idea from the deleted plan)

- **Done (WI-0, D13):** every adapter records a typed `json_path` per block at parse time (`Block.json_path`, persisted as `blocks.json_path`, a JSON array such as `["messages", 3, "content", 1]`); never inferred by substring search. Historical rows are backfilled only where the canonical document is retained and the parse matches exactly; otherwise NULL. See "State of the code".
- **Still to build:** a *controlled* JSON viewer so ancestors can be expanded and the node scrolled into view, and the states to distinguish: legacy capture without path, canonical body purged/archived, path no longer resolves.

## Performance

One bulk query per tree (no N+1); lazy tokenization of the selected block only; load the canonical
body lazily for the selected request.

## Open questions

1. Exact node taxonomy per mode and what counts as a "group" node.
2. Scope of first version: single request tree first, or conversation tree first? (Suggest single
   request + "introduced" mode first, since compare (plan 6) builds on it.)
3. URL state (`?request=&node=&mode=`) for shareable links — likely yes.
4. Where the page is entered from (Request detail button, conversation view, session header).
5. Should the "By turn" mode wait for turn grouping (`turn_index` / `turn_start_request_id`, not implemented, see `request-purpose.md`), or can the first version derive turns request-locally from message order?
6. Is a conversation-scoped tree acceptable before the cold conversation analysis is faster (issue #68; ~65 s on a 4,032-request session), or should the first version be single-request?
7. Which parts of the Hot spots toolbar (scope select, group/sort controls) should the tree share? Wait for the author's styling remarks on the Hot spots page first (D9).

*Status of these questions (user, 2026-10-06): all seven are deliberately left open; review them when work on this plan starts.*

## State of the code (2026-10-06): what this draft can build on, and what changed

Everything below was verified against the code on the date above; plans 1-4 are implemented and committed on `analysis_revamp`.

**Per-block data already on the block payload** (`BlockRecord.to_dict`, `GET /api/requests/{id}/blocks`): `source_key` (e.g. `tool:Read`, `mcp:github/create_issue`, `bash:git`, `exec:rg`), derived `activity` (read, search, edit, vcs, test, command, web, orchestration, mcp, other), `file_path` (read/edit tool calls and their results only; ~8% of blocks in the author's sample), `json_path` (array or null), `linked_call_id`, `linked_definition_id`, `first_seen_session_seq`, `attrs` (incl. `attrs.source.calls` / `.files` details). **Requests** carry `purpose` (`user_turn`, `tool_continuation`, `compaction`, `unknown`; `housekeeping` reserved) and `purpose_detail` (`trailing_tool_results`, `has_user_text`, `response.kind`/`tool_calls`).

**Not available yet:**
- **Turn grouping.** `turn_index` / `turn_start_request_id` on lineage nodes (spec: `request-purpose.md`, "Turn grouping") is not implemented. The "By turn" mode needs it; it must be derived at read time from the lineage graph (never persisted) and cached with it.
- **Typed JSON reveal.** The content viewers were rebuilt since this draft was written: the old `RawViewer.JsonNode` remark is obsolete. Today `ui/src/components/ui/content-viewer/` has `JsonTreeView` plus `useTreeExpansion(paths, contentKey)` (collapse state lives in the parent; paths look like `$/messages/3/content/1` with `~`/`/` escaped as `~0`/`~1`, built by `jsonCollapsiblePaths`). There is no "expand ancestors and scroll to path" API yet; translating a stored `json_path` array into the viewer's path format is a one-line mapping, and `lib/jsonPath.ts: formatJsonPath` already renders it as `messages[3].content[1]` for display.
- **Ollama** emits no tool-call/tool-result blocks, so a tree for Ollama requests has no tool structure.

**Reusable pieces:**
- *Info panel:* `components/request/BlockInspector.tsx` (details, source, activity, file, JSON location, "Present in") and `BlockOccurrences.tsx`; `?block=<id>` selects a block in Request detail and `/requests/{id}?block={block_id}` is the established deep link (block ids are request-local row ids; they change if a migration rebuilds a request's blocks).
- *Scope:* `db/block_occurrence_service.py: scope_for_session(db, session_id, scope, anchor_request_id=, conversation_key=, with_conversations=)` is the one place that turns (session, conversation) into an ordered request list; positions are indexes in *scope order* (other conversations' interleaved requests do not count). A tree's conversation scope must use it (D5).
- *Labels without content:* `analysis/block_hotspots.py: block_label` builds a short label from stored structure only (works after archive); `lib/blockVisuals.ts` (`visualForType`, colours, short letters) maps types to the visual language; category labels/colours are in `components/ContextBar.tsx`.
- *Hot spots page* (`components/hotspots/HotSpots.tsx`) is the closest visual sibling (rows, share bars, badges, toolbar with `SegmentedControl`); the author has styling remarks pending, so wait for them before copying its look (D9).
- *Diff:* `analysis/context_diff.py` (`diff_contexts`, `ContextDelta.removed` exists) and `GET /requests/{id}/context-diff?parent_id=`.
- *Bulk loading:* `crud.get_blocks` is one query per request; the occurrence and hot-spots services show the pattern for constant-statement-count tests and for scope temp tables.

**Performance facts that constrain a conversation-scoped tree (samples):** the conversation membership (existing lineage analysis) takes ~65 s cold on a 4,032-request session and ~7 s on a 570-request one, ~5 s on a warm graph, and is cached for 60 s once built; session scope does not need it (0.1 s). Listing the requests of an unarchived session costs ~0.4 s per 570 requests because of large inline body columns. Plan the first version around a single request (or a bounded window) to stay clear of this, or fix it first (issues [#68](https://github.com/RimantasZ/contextspy/issues/68) and [#69](https://github.com/RimantasZ/contextspy/issues/69), draft plans `perf-*.md` in this folder).

**Archived sessions:** block rows, hashes, tokens, sources, file paths and `json_path` survive; text, previews, raw and canonical JSON do not (`Request.content_state`, `ContentStateNotice`). The Content and Raw JSON tabs need an honest "removed by archive" state; nodes stay visible with token counts (D3).

**Fidelity (D11):** opaque/partial requests hide part of their window; a tree shows only visible blocks, and totals say "visible tokens".

**Additional open questions raised by the code** are numbered 5-7 in "Open questions" below.
