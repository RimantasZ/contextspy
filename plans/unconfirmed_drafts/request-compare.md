# Draft 6: Request compare

Status: DRAFT (not approved; kept as a draft on purpose until after the first release, roadmap D23). Updated 2026-10-06 with "State of the code" below. Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [context-tree](context-tree.md) (and transitively info panel, request purpose).

## Goal

Compare two requests using the tree view, showing the request trees side by side with colour-coded
changes: blocks **added**, **removed**, and **same slot with different content**.

## Design direction (proposed)

- **Delta summary first**: the common question is "why did tokens jump here?". Top of page:
  `+8,214 tokens: 1 tool result, 1 file read` (computed in Python from the diff; tokens only, D1).
- Default pair: a request vs its baseline (lineage parent, else previous in conversation — same
  rule as `plans/archive/show-new-block-only.md`), one click "compare with previous". Any two requests
  selectable (also across conversations; then no lineage assumption).
- Reuse `analysis/context_diff.py` (`diff_contexts`: persisted / promoted / replaced / added;
  `BlockReplacement` for same-slot changes). Add removed blocks if not already exposed
  (`ContextDelta` — verify).
- Implementation approach: **one tree annotated with added/removed/changed/unchanged states**, with
  side-by-side as a layout option, so the tree component is not duplicated.
- Backend endpoint returns the annotated structure; frontend only renders (policy). Existing
  `GET /requests/{id}/context-diff?parent_id=` is the starting point; it returns `new_child_block_ids`.
- Same visual language as Request detail and the context tree (D9). Info panel on the right shows
  the node, and for "changed" nodes a text diff of old vs new content (content viewers; handle purged
  content gracefully, D3).
- Entry points: panel action "Compare with…" in the info panel (reachable from Request detail,
  conversation view, session views — D8).

## Open questions

1. Text diff granularity for changed blocks (line diff vs token diff) and size limits.
2. Compare across sessions allowed?
3. How "moved" blocks (same hash, different position) are shown.
4. Handling of lineage breaks (compaction): compare pre- and post-compaction requests, show what was dropped.

## State of the code (2026-10-06)

- **Removed blocks are already exposed:** `analysis/context_diff.py: ContextDelta` has `removed` (alongside `persisted`, `promoted`, `replaced`, `added`) and counts per category/type; `GET /requests/{id}/context-diff?parent_id=` returns `new_child_block_ids`. The earlier "verify" note is resolved; the endpoint still needs an annotated-structure variant for this feature.
- **Pairing "same slot, different content"** has two sources today: `BlockReplacement` (position-based) and, new, **`blocks.file_path`** (same file, different content hash) which gives a content-independent way to pair versions of one file (coverage is partial: ~8% of blocks in the author's sample; relative and absolute spellings of one file differ). Fuzzy/near-duplicate pairing is the v2 similarity idea (`similarity-grouping.md`).
- **Text diffs need stored content:** archive removes block text (D3), so a diff of archived requests can show only structure and token deltas; `content_purged` / `Request.content_state` tell the UI which case it is. Previews in hot spots show how a purged block should degrade.
- **Block ids are request-local row ids**; a compare URL should carry two request ids and optional block ids, and must tolerate a block id that no longer resolves.
- **Lineage breaks:** the baseline rule (parent, else previous in conversation) is implemented for "Show: New only / Highlight new" in Request detail; a request whose purpose is `compaction` marks a likely break and is a natural first compare target for open question 4. Conversation membership and ordering come from `scope_for_session`; across conversations there is no lineage assumption.
- **Cold-membership cost (65 s on a 4,032-request session) applies** to anything that resolves a conversation; comparing two explicitly chosen requests does not need it.
- Visual language: wait for the author's styling remarks on the Hot spots page and the tree page decisions (D9).

