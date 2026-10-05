# Draft 5: Context tree page

Status: DRAFT (not approved). Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [info-panel](../info-panel.md), [request-purpose](../request-purpose.md).
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

- Add a typed `json_path` per block, recorded at adapter parse time (`["messages", 3, "content", 1, "text"]`),
  nullable; **never** infer by substring search. Needs `Block.json_path`, every adapter
  (`anthropic.py`, `openai_chat.py`, `openai_responses.py`, `ollama.py`), `BlockRecord.json_path`
  (additive column + migration; historical backfill only when canonical bodies are still retained
  and the match is unambiguous). **All adapters in one change (D13)**; verify OpenAI Responses/Codex first (93% of data).
- Controlled JSON viewer (collapse state owned by parent) so ancestors can be expanded and the node
  scrolled into view. Current `RawViewer.JsonNode` owns collapse state itself.
- States to distinguish: legacy capture without path, canonical body purged/archived, path no longer resolves.
- **Check the current canonical JSON state before starting**: `requests.canonical_request_body` exists;
  the old "JSON reconstruction plan" referenced by the deleted plan is not in `plans/` — confirm whether
  its goals were met.

## Performance

One bulk query per tree (no N+1); lazy tokenization of the selected block only; load the canonical
body lazily for the selected request.

## Open questions

1. Exact node taxonomy per mode and what counts as a "group" node.
2. Scope of first version: single request tree first, or conversation tree first? (Suggest single
   request + "introduced" mode first, since compare (plan 6) builds on it.)
3. URL state (`?request=&node=&mode=`) for shareable links — likely yes.
4. Where the page is entered from (Request detail button, conversation view, session header).
