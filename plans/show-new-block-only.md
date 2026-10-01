# Plan: "Show" control — new-only / highlight-new blocks in Request detail

Status: implemented

## Goal

Add a **Show** dropdown to the block toolbar on the Request detail page, next to **Size**, with:

| Option | Behaviour |
|---|---|
| **All** (default) | Current behaviour. |
| **New only** | Display only blocks that were *not* present in the previous request. |
| **Highlight new** | Render the block view exactly as the other controls (Arrange, Size, type filters, search, hide-zero, view mode) dictate, but draw blocks that *were* already present in the previous request at 50% opacity. |

## Decisions (agreed with user)

1. **Baseline = lineage parent.** The "previous request" is the `context_continuation` parent edge that `RequestDetail` already resolves (`parentEdge`, exact or inferred). This stays correct when subagent/parallel requests interleave in a session. No parent → no baseline (see *Edge cases*).
2. **Matching = reuse `analysis/context_diff.py`** (`diff_contexts`: occurrence-aware, handles persisted / promoted / replaced / added). Per the "analysis lives in Python" policy in `AGENTS.md`, the *new vs. existing* decision is made in the backend; the frontend only receives a list of ids and filters/dims.
3. **Request direction only.** On the Response tab the Show control is disabled (same pattern as `arrangementDisabled` for Arrange) with a tooltip; response blocks are always new, so nothing is filtered or dimmed.
4. Blocks the diff cannot classify (`unavailable_child_blocks` — no content hash and no tool_call_id, e.g. purged/structural) are treated as **new**, so "New only" never silently hides something we couldn't verify.

## Definition of "new" (backend)

Using the existing `ContextDelta` from `diff_contexts(parent.blocks, child.blocks)` for the child's **input** blocks:

- **Existing (not new):** child ids in `persisted` and `promoted` (promoted = parent *output* that became child *input*, i.e. it was already in the conversation).
- **New:** child ids in `added`, child ids in `replaced` (content changed in the same slot), and `unavailable_child_blocks`.

Equivalently `new = all child input block ids − (persisted ∪ promoted child ids)`. Computing it as the complement guarantees every input block is classified exactly one way.

## Implementation steps

### 1. Backend — expose the new-block set (no schema change)

No `models.py` change → no migration step needed.

- `contextspy/analysis/context_diff.py`: add a helper
  `new_child_block_ids(delta: ContextDelta, child_blocks: Iterable[ContextBlock]) -> list[int]` implementing the complement rule above (input direction only).
- `contextspy/analysis/lineage.py: context_diff_for_requests`: add `"new_child_block_ids": [...]` to the returned dict. Purely additive, so existing consumers (`SessionLineage`) are unaffected.
- `contextspy/api/routers/requests.py`: no new route — the existing `GET /requests/{id}/context-diff?parent_id=` already returns this payload.
- Tests (`tests/test_lineage.py` or a new `tests/test_context_diff.py`, matching where `diff_contexts` is tested today):
  - unchanged context → no new ids;
  - appended turn → only the appended blocks are new;
  - parent response promoted into child input → not new;
  - edited block in the same slot (`replaced`) → new;
  - block with no hash/tool_call_id → new;
  - duplicate identical blocks (occurrence-aware): a second copy added is new, the first is not.

### 2. Frontend — data plumbing

- `ui/src/api/client.ts`: add `new_child_block_ids: number[]` to `ContextDiffResponse`.
- `RequestDetail.tsx`: **own the Show state here**, next to `activeDirection`: `const [showMode, setShowMode] = useState<ShowMode>('all')`, passed down as `showMode` / `onShowModeChange`. See *Show-mode lifetime* below for why it can't live in `RequestWorkbench`. Also pass `parentRequestId={parentEdge?.source_request_id ?? null}` to `RequestWorkbench` (it already computes `parentEdge`; also pass a `lineageLoading` flag from `lineage.isLoading` so the control doesn't flash "no previous request" while loading).
- `RequestWorkbench.tsx`:
  - Receives `showMode` / `onShowModeChange` as props (no local state for it); export `type ShowMode = 'all' | 'new' | 'highlight'`.
  - `const diff = useContextDiff(request.id, parentRequestId)` — the hook already exists; verify it is disabled when `parentId` is null (otherwise gate with `enabled`).
  - `const newIds = useMemo(() => new Set(diff.data?.new_child_block_ids ?? []), [diff.data])`.
  - Effective mode: `activeDirection === 'output' || !diff.data ? 'all' : showMode`.
  - Feed it into `buildWorkbenchBlockModel` (see step 3) so filtering composes with the other controls and `visibleBlocks` stays the single source of truth for maps, legend, keyboard nav and "Jump to largest".
  - `dimmedIds` (highlight mode): `Set` of `visibleBlocks` ids not in `newIds`; passed to the map components.
  - `jumpTo(targetId)`: if the target is hidden by "New only", reset `showMode` to `'all'` (mirrors how it already clears search / re-enables the type filter / un-hides zero-token).

### 3. Frontend — filtering (`ui/src/lib/blockArrangement.ts`)

- Extend `buildWorkbenchBlockModel` options with `newOnly?: { newIds: ReadonlySet<number> } | null`; when set, drop blocks whose id is not in `newIds` (in the same `filter` as hide-zero / type / search). No classification logic here — it only applies the backend-provided id set.
- Unit test in the existing `blockArrangement` test file (or `RequestWorkbench.test.tsx`): new-only composes with hide-zero + type filter + search; `available` (type chips) still derives from all direction blocks so chips don't vanish.

### 4. Frontend — toolbar control (`BlockToolbar.tsx`)

- New props: `showMode`, `showDisabled`, `showDisabledReason`, `onShowMode`.
- Add right after the **Size** `<label>`:
  ```tsx
  <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
    Show
    <select value={showMode} disabled={showDisabled} title={showDisabled ? showDisabledReason : undefined}
            onChange={…} className="app-field py-1.5">
      <option value="all">All</option>
      <option value="new">New only</option>
      <option value="highlight">Highlight new</option>
    </select>
  </label>
  ```
- Disabled reasons: Response tab → "Available for request blocks only"; no parent → "No previous request in this conversation to compare against"; diff loading → "Loading comparison…"; diff failed → "Could not load comparison".

### 5. Frontend — 50% opacity rendering

- `BlockTile.tsx`: add `dimmed?: boolean` → adds `opacity-50` (Tailwind) to the button className. Keep the selected focus ring fully visible (apply opacity only when `!selected`, so a selected block is never faded). Append "(already in previous request)" to the `aria-label`/`title` when dimmed so the state isn't conveyed by opacity alone.
- `ProportionalBlockMap.tsx` and `CompactBlockMap.tsx`: accept `dimmedIds?: ReadonlySet<number>` and pass `dimmed={dimmedIds?.has(block.id)}` to `BlockTile`.
- `BlockLegend.tsx` takes `visibleBlocks`, so it automatically reflects the "New only" subset; no change needed.

### 6. Frontend tests (Vitest/RTL, `RequestWorkbench.test.tsx` + map tests)

- Show dropdown renders next to Size with the three options; defaults to All.
- "New only": only blocks in `new_child_block_ids` rendered; others absent.
- "Highlight new": all blocks rendered; existing ones have `opacity-50`, new ones don't; selected existing block is not dimmed.
- Disabled on Response tab and when `parentRequestId` is null; switching back to Request re-enables it.
- Show mode composes with Arrange/Size/type filters (e.g. Arrange = Turn + New only).
- `jumpTo` a hidden block resets Show to All.

### 7. Build & verify

- `pytest` (backend), `cd ui && npm test`, then `make ui` (the served UI is the pre-built `contextspy/_web/`).
- Manual check against a real multi-request session: Request #N vs. its parent — "New only" should show roughly the appended turn plus anything edited; "Highlight new" should show the full map with the stable system prompt / tool definitions / earlier history faded.

## Show-mode lifetime (decided)

The choice **resets when the user leaves the Request detail page**, but **persists when navigating to another request from within it** (Parent / Child buttons, which `navigate('/requests/<id>')`).

Mechanism: both routes render the same `RequestDetail` element, so React Router keeps that component mounted across `:id` changes, and state held in `RequestDetail` survives; leaving the page unmounts it and state is discarded. This is the same mechanism `activeDirection` already uses.

Why not in `RequestWorkbench`: `RequestDetail` early-returns a "Loading request…" div while `useRequest(id)` loads the new id, which unmounts `RequestWorkbench` and would lose its local state on every parent/child hop. Lifting the state avoids this. No `localStorage`/URL param is used, since persisting beyond the page is explicitly not wanted.

Interaction with the baseline: after a hop the baseline automatically becomes the *new* request's parent. If the new request has no parent, the control shows disabled and the request renders as All, but the stored `showMode` is retained (not reset), so it applies again on the next request that has a parent.

Test: render `RequestDetail` at `/requests/a`, set Show = New only, navigate to `/requests/b` → still New only; unmount / navigate to another route and back → All.

## Edge cases

- **No lineage parent** (first request in a conversation, or unresolved/ambiguous predecessor): Show is disabled, behaves as All.
- **Inferred parent** (`certainty === 'inferred'`): works the same; the header already labels it "(N% inferred)". Optionally add that qualifier to the control's tooltip.
- **Parent request deleted or blocks purged**: backend returns 404 / `unavailable_*` ids; 404 → control disabled with "Could not load comparison"; unavailable blocks count as new.
- **Selection**: the existing effect in `RequestWorkbench` already clears selection when the selected block leaves `visibleBlocks`, so hiding via "New only" is handled.
- **Keyboard navigation / roving tabindex** operate on `visibleBlocks` and are unaffected by dimming.
- **Type-chip token totals** come from the backend for the whole direction and stay unchanged in "New only" (they describe the request, not the filtered view). Listed under open questions.

## Out of scope

- Comparing against an arbitrary request (baseline picker).
- Changing the Response direction behaviour.
- Raw view (the Show control only applies to Default/Compact map views; hide or disable it in Raw view like the rest of the toolbar, which is only rendered outside Raw).
- Persisting the Show choice beyond the Request detail page (page reloads, other routes).

## Open questions / small defaults to confirm

1. Should the type-chip token totals and legend reflect only the new blocks while in "New only"? Default in this plan: legend yes (it follows `visibleBlocks`), chip totals no (they are request-wide).
2. ~~Persist across requests?~~ Resolved: persists across parent/child navigation within the page, resets on leaving it (see *Show-mode lifetime*).

## Files touched (expected)

Backend: `contextspy/analysis/context_diff.py`, `contextspy/analysis/lineage.py`, tests under `tests/`.
Frontend: `ui/src/api/client.ts`, `ui/src/pages/RequestDetail.tsx`, `ui/src/lib/blockArrangement.ts`, `ui/src/components/request/{RequestWorkbench,BlockToolbar,BlockTile,ProportionalBlockMap,CompactBlockMap}.tsx` and their tests.
