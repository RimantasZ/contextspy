# Plan: Hot spots page layout (profiler-style bars) — DRAFT for review

Status: **implemented, uncommitted, awaiting the author's browser review** (2026-10-06). Decisions below were confirmed: `summary.occurrences_total` added (all block instances of the filtered scope, the same population as the token total), header click toggles the sort, `dropped` badge stays on the collapsed row, 2 px minimum bar. Both label variants are built behind `layout=name|bar`; removing the loser is planned in [../postponed/hot-spots-label-layout-removal.md](../postponed/hot-spots-label-layout-removal.md); the release ships with both. Follows [hot-spots-layout-handoff.md](hot-spots-layout-handoff.md); the data/API plan is [hot-spots.md](hot-spots.md).
Rules from the hand-off still apply: D9 (same tokens/classes as Request detail), current colours and styling kept, presentation only, the user commits.
Everything in "Earlier observations" of the hand-off (repeated %, icons, path truncation, mobile) is **deferred** until this change is in; re-check which still apply afterwards.

## Author's requests
1. Look like a profiler hot-spot table (reference: a heap profiler class list: Name | bar + number | size columns).
2. Bar length proportional to the **main metric**; the metric follows the *Sort by* control: total tokens or occurrences.
3. Main metric on the right with the % of total in parentheses; the other metric goes into the expandable detail.
4. Rows expand on click (like the request list on the dashboard); the second-line information moves into the expanded part.
5. Rows more compact vertically.
6. Label strategy: inside the bar when the bar is big enough, otherwise outside to its right.
7. First column holding type/tool/purpose, if useful.

## Decisions taken in the clarification round
- **Bar scale:** the largest row (the first, since rows come sorted by the metric) = full width; the others scale to it. The scale is fixed by the first row, so *Show more* does not rescale bars already shown.
- **Click:** expands/collapses the row; "Open the latest request" is a button in the expanded part (Sources also keep "Show the blocks").
- **Sources and Files:** same rule as Blocks (main metric follows Sort; files keep read/edited tokens and versions in the detail).
- **Label placement:** the author wants to compare two variants; build **both behind a temporary toggle** and remove the loser (see below).

## Layout

Rows become a CSS grid (one grid template shared by the header and every row, so columns align):

```
 ▸ [T]  Name                    |████████████████████  786,149 (12.3%)
 ▸ [T]  bash cat call           |████████               33,102 (0.5%)
```

- **Header row** (small, muted): `Name` and the metric column titled by the current sort (`Total tokens` / `Occurrences`) with a sort marker. The *Sort by* segmented control stays in the toolbar (tests and deep links use it); making the header clickable is optional later.
- **Chevron** (`▸`/`▾`, `aria-expanded`) at the start of the row.
- **Type chip** (current coloured letter square, smaller) = the "first column". For blocks it is the block type; its tooltip says `type · category · activity`. For sources the activity chip, for files the result chip, as today. A text column of tool/purpose is **not** added now (the chip plus tooltip is enough; revisit if the author wants the words visible).
- **Number column**: main metric, `tabular-nums`, right aligned, `12.3%` muted in parentheses. Bar colour = the existing `--accent`; track = `--surface-muted`.
- **Compact rows**: `py-1`, single line, ~28 px; no second line, no per-row "% of visible tokens", no per-row share bar. Warning badge `dropped` stays visible on the collapsed row (it is short and decides what to look at); `reappears` / `N types` move into the detail.

### Variant A — profiler style (name column)
`grid: chevron | chip | Name (minmax(0, 2fr), truncate, full text in title) | bar+number (minmax(0, 3fr))`.
The bar and number share the last column: the bar is a thin-ish fill behind/left of the number like the reference. Label length never competes with the bar. The label is truncated with `…`; the title attribute and the expanded part hold the full label.

### Variant B — label on the bar
`grid: chevron | chip | bar+label+number (1fr)`; the label lives in the bar column:
- bar wide enough for the label → label **inside** the bar (light text on `--accent`; check contrast in both themes);
- otherwise → label **outside, right after the bar end**, in normal text colour, truncated so it never runs into the number column;
- the number column stays at the right in both cases.
How "wide enough" is decided: measure the label once (`ResizeObserver` on the bar column + a hidden measuring span, or `scrollWidth` of the label), fit = text width + padding ≤ bar pixel width. jsdom has no layout, so the component takes a pure function `labelFits(textWidth, barWidth)` and the fallback estimate (chars × ~6.5 px) is used when measurement is unavailable; tests exercise the function and the DOM class, not pixels.

### Toggle (temporary)
A small *Labels: Name column | On the bar* segmented control in the toolbar, stored in the URL as `layout=name|bar` (default `name`) so the author can compare by link. Once one variant is chosen: delete the other, the toggle, `layout` URL param and its tests, and note the decision in the roadmap.

### Expanded detail (all groups)
Indented under the row, `--surface-muted` background, two columns of `label: value` pairs:
- **Blocks:** the *other* metric (tokens each or total tokens when sorted by occurrences; occurrences when sorted by tokens), requests, tokens each (or "sizes differ"), seq range `#a–#b`, runs / *reappears*, *N types*, tool and source, file path, the full preview (≤120 chars) or "content no longer stored", the full label. Button **Open latest request**.
- **Sources:** activity, distinct blocks, occurrences, requests, largest block tokens. Buttons **Open the largest block**, **Blocks** (re-filter).
- **Files:** read tokens, edited tokens, versions, occurrences, requests, seq range. Button **Open latest request**.
Only one row open at a time? **No**: independent, local state per row (`Set` of keys in the component; not in the URL).

## One API addition needed (decision requested)
"% of total" in occurrences mode needs the **total occurrences of the scope**, which the response does not have (`summary` has only token totals). Smallest change: `summary.occurrences_total` = sum of `occurrence_count` over all listed groups (the aggregate table already holds it, so it is one `SUM` over `hs_agg`, no extra pass; the unidentifiable group excluded, like the token share). The UI computes `occurrence_count / occurrences_total`. This is an API change: service + tests (including the query-plan test staying green), SPEC.md API table, `SessionHotspots` type. Alternative without an API change: show the % only in tokens mode and a plain number in occurrences mode (the author asked for the % in both).
**Recommendation:** add the field (tiny, same pass).

## Changes by file
- `ui/src/components/hotspots/HotSpots.tsx`: replace `RowShell`/`ShareBar` with `HotspotTable` (header + grid rows), `HotspotRow` (collapsed row, bar, label placement), `Detail*` components per group; keep the toolbar, summary, show-more and states as they are; add `layout` param to `HOTSPOT_URL_PARAMS`.
- Possibly split into `hotspots/HotspotRow.tsx` + `hotspots/labelFit.ts` (pure function) to keep the file readable.
- `ui/src/api/client.ts`: `summary.occurrences_total` (if accepted).
- Backend (only if accepted): `db/hotspots_service.py`, `tests/test_hotspots.py`, `SPEC.md`.
- `HotSpots.test.tsx`: update (see below). `SessionDetail.test.tsx` should not change.
- Docs: `plans/archive/hot-spots.md` (layout section), roadmap status line, handoff file superseded note, `docs/changelog.md`.

## Tests that change deliberately
- Row buttons named `Open the latest request carrying <label>` etc. move into the expanded detail: the test first clicks the row (`Show details of <label>` toggle with `aria-expanded`), then the open button. Same names for the open buttons, new name for the toggle.
- Texts that move into the detail ("reappears", "N types", "tokens each") are asserted after expanding; "dropped" stays asserted collapsed.
- New: bar widths (style width % relative to the first row, min visible width), metric follows Sort (tokens vs occurrences, % in parentheses), the secondary metric appears in the detail, `labelFits`, layout toggle + URL param, only the first-row scale after *Show more*.
- Unchanged: toolbar labels, URL parameters, *Show more*, states, summary strings, conversation link.

## Open questions for the review
1. Accept the `summary.occurrences_total` API addition (recommended)?
2. Header column titles only, or also clickable to sort (the Sort control would stay)?
3. `dropped` badge on the collapsed row: keep (proposed) or move into the detail with the others?
4. Minimum bar width for very small values (profiler shows a 1–2 px sliver): proposal 2 px.
5. Label text inside the bar needs a light colour on `--accent`: if contrast fails in one theme, use the dark text on a lighter tint of the accent for the bar instead. Check in the browser when variant B exists.

## Out of scope
Virtualisation, compact number units, mobile polish, icon set, new groupings (#70), result cache (#67). Re-evaluate after the author has seen the new layout.
