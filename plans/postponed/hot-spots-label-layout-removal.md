# Postponed: remove one Hot spots label layout (and the switch)

Status: **postponed** until users have tried the release. Decision pending: keep `name` (label in its own column) or `bar` (label on the bar). The release ships with both. Background: [../archive/hot-spots-layout.md](../archive/hot-spots-layout.md).

## When to do it
After feedback from the release. Ask the author which variant won before touching code (the author decides, not the agent).

## What to remove (same for either winner)
- `ui/src/components/hotspots/HotSpots.tsx`
  - the `Layout` type, the `layout` URL parameter (`'layout'` in `HOTSPOT_URL_PARAMS`) and the `pick<Layout>` line;
  - the *Labels* `SegmentedControl` in the toolbar;
  - `gridColumns(layout)` becomes one constant template; `TableHeader` loses its `layout` prop and the `layout === 'name'` spacer.
- `HotspotRow`: keep only the winner's branch.
- Tests: `HotSpots.test.tsx` "can put the label on the bar (layout=bar)" goes (or becomes the winner's test); other tests need no change.

## If `name` wins
- Delete `BarCell`'s label logic (`label` prop, `trackRef`/`labelRef`, `useLayoutEffect`, `placement`) and the `LabelPlacement` import.
- Delete `ui/src/components/hotspots/labelFit.ts` functions `labelPlacement`, `ESTIMATED_CHAR_WIDTH`, `ESTIMATED_TRACK_WIDTH` and their tests in `labelFit.test.ts`; keep `barPercent` and `sharePct`.
- Badges stay next to the name.

## If `bar` wins
- Delete the Name column branch and the 5-column template (`'1rem 1.25rem minmax(0, 2fr) minmax(0, 3fr) 9.5rem'`).
- Keep `labelFit.ts` as is. Re-check dark-theme contrast of `--text-on-accent` on `--accent`, and very long labels (paths), before finalising.

## Also
- Update `plans/archive/hot-spots-layout.md` (status, decision), `plans/ANALYSIS_ROADMAP.md` status line, `docs/changelog.md`, and delete this file or move it to done.
- Run `cd ui && npm run check` and `make ui`. No backend change.
