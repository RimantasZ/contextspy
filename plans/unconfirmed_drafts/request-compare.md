# Draft 6: Request compare

Status: DRAFT (not approved). Part of [../ANALYSIS_ROADMAP.md](../ANALYSIS_ROADMAP.md).
Depends on: [context-tree](context-tree.md) (and transitively info panel, request purpose).

## Goal

Compare two requests using the tree view, showing the request trees side by side with colour-coded
changes: blocks **added**, **removed**, and **same slot with different content**.

## Design direction (proposed)

- **Delta summary first**: the common question is "why did tokens jump here?". Top of page:
  `+8,214 tokens: 1 tool result, 1 file read` (computed in Python from the diff; tokens only, D1).
- Default pair: a request vs its baseline (lineage parent, else previous in conversation — same
  rule as `plans/show-new-block-only.md`), one click "compare with previous". Any two requests
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
