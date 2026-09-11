# ContextSpy UI redesign implementation plan

> **Status:** Implemented with documented deviations on `ui-revamp-poc` (September 2026). The
> authoritative current behavior is in [`SPEC.md`](../SPEC.md#56-web-ui); this file remains the
> original delivery plan. The shipped **Proportional** view uses a capped logarithmic whole-block
> span instead of token-linear split segments, zero-token blocks are visible by default with muted
> styling, selected content appears below the map beside a metadata inspector, and only the theme
> preference is persisted.

## Goal

Make request composition the primary interaction in ContextSpy, introduce compact and accurately proportional block views, eliminate page-level overflow, replace the tool-composition donuts with a treemap, and refresh the application with a light, pastel visual system.

This plan is intentionally front-end focused. The existing request, request-block, tool-stat, session, and tokenize APIs contain the data needed for the redesign.

## Current implementation map

- `ui/src/pages/RequestDetail.tsx` owns request metadata, category charts, tool analytics, and the collapsed Request/Response viewers.
- `ui/src/components/RawViewer.tsx` owns the outer Request/Response accordions and response tabs.
- `ui/src/components/ParsedViewer.tsx` fetches input blocks, renders the weighted overview, renders the full-width parsed list, and tokenizes all blocks on load.
- `ui/src/components/ToolBreakdown.tsx` renders the long tool legend, Definitions donut, Results donut, and usage table.
- `ui/src/components/RequestTable.tsx` renders eleven columns, including a Context column with a 256px minimum width.
- `ui/src/components/Layout.tsx` uses a fixed 192px sidebar at every viewport width.
- `ui/src/components/TokenDonut.tsx` assumes a side-by-side 50/50 donut and table, which clips inside narrow cards.
- `ui/src/index.css` currently contains only the Tailwind directives; there is no semantic theme-token layer.
- The UI has a build command but no automated component or layout test setup.

Important implementation constraints:

- `RequestBlock.position` provides stable request ordering.
- Server-provided `token_count` is sufficient for both compact and proportional layout; tokenization is only necessary for token-highlighted content.
- Blocks can contain purged content while retaining structural data and token counts.
- Linked block IDs must continue to support previous-message, call, and definition navigation.
- Hundreds of blocks are normal, so the default view must avoid hundreds of expanded DOM-heavy content panes.

## Target page structure

The request-detail route should render:

1. `RequestSummaryHeader`
2. `CaptureNotice`, when needed
3. `RequestWorkbench`
   - Request / Response tabs
   - Compact / Proportional / Raw view controls
   - Filters and grouping controls
   - Block canvas
   - Persistent `BlockInspector`
4. Collapsible Analytics section
   - Category composition
   - Tool treemap and table
5. Collapsible Metadata section

Request should be the default active tab and the workbench should be visible without another click.

## Phase 1: establish the responsive shell and design tokens

### Files

- Modify `ui/src/index.css`.
- Modify `ui/tailwind.config.js` if Tailwind aliases are useful.
- Modify `ui/src/components/Layout.tsx`.
- Add small shared primitives under `ui/src/components/ui/` as needed, for example `Panel.tsx`, `Badge.tsx`, `SegmentedControl.tsx`, and `IconButton.tsx`.

### Work

1. Define semantic CSS variables for canvas, surface, elevated surface, text, muted text, border, focus, success, warning, danger, and each block type.
2. Adopt the light palette from the critique. Use one-pixel near-black borders on composition blocks and neutral gray borders on ordinary panels.
3. Add global `box-sizing`, focus-visible, font smoothing, tabular-number, and overflow safeguards.
4. Ensure application-shell children use `min-width: 0` and the main region never forces document-level horizontal scrolling.
5. Convert the sidebar into:
   - Full wordmark navigation on wide screens.
   - Compact icon rail below approximately 1100px.
   - Drawer or top navigation below approximately 720px.
6. Reduce the logo footprint and preserve accessible navigation labels in compact modes.
7. Apply the new primitives first to the shell and new workbench; migrate remaining pages incrementally to avoid an all-at-once rewrite.

### Exit criteria

- The shell has no horizontal document overflow at 1440, 1240, 1024, 768, and 390px.
- Navigation remains usable with mouse, keyboard, and screen reader at each breakpoint.
- New semantic colors meet WCAG AA contrast for text and interactive controls.

## Phase 2: reorganize Request detail around the workbench

### Files

- Refactor `ui/src/pages/RequestDetail.tsx`.
- Refactor or replace the outer-accordion responsibility in `ui/src/components/RawViewer.tsx`.
- Add `ui/src/components/request/RequestSummaryHeader.tsx`.
- Add `ui/src/components/request/RequestWorkbench.tsx`.
- Add `ui/src/components/request/CaptureNotice.tsx` if the notice logic warrants extraction.

### Work

1. Replace `requestToggle` and `responseToggle` with explicit workbench state, such as `activeDirection: 'input' | 'output'`.
2. Make the request workbench open by default and place it directly below the summary and warning.
3. Convert Context tokens and Generated tokens from remote toggle buttons into compact summary metrics. Selecting a metric may switch the workbench direction but must not scroll the page unexpectedly.
4. Reduce the visible metadata to the fields needed for scanning: status, provider/model, timestamp, duration, context/output totals, and cache usage.
5. Move the remaining API-reported and diagnostic fields into a disclosure section below the workbench.
6. Put category and tool analytics into a secondary, collapsible Analytics section.
7. Preserve loading, not-found, opaque-state, capture-error, reconstructed-response, incomplete-response, and purged-content states.
8. Keep Raw payload access as an advanced workbench view rather than a separate page-bottom accordion.

### Exit criteria

- At 1280×720, the workbench controls and the first composition rows are visible without scrolling.
- Request and Response are reachable through one stable tab control.
- Opening content does not cause a scroll jump or replace the user's current analytical context.

## Phase 3: implement compact and proportional block views

### Files

- Split `ui/src/components/ParsedViewer.tsx` into focused components under `ui/src/components/request/`:
  - `BlockToolbar.tsx`
  - `CompactBlockMap.tsx`
  - `ProportionalBlockMap.tsx`
  - `BlockInspector.tsx`
  - `BlockLegend.tsx`
- Add `ui/src/lib/blockLayout.ts` for pure layout calculations.
- Add `ui/src/lib/blockVisuals.ts` for labels, stable type colors, and accessible names.
- Retain a slim compatibility component in `ParsedViewer.tsx` or replace its imports after the split.

### Compact mode

1. Sort input blocks by `position` and render exactly one equal-size tile per block in a responsive CSS grid.
2. Use a tile size in the 22–28px range on desktop, with an optional density control rather than shrinking below a usable target.
3. Use pastel fill plus a one-pixel near-black border. Selection should use a distinct outline and `aria-pressed`.
4. Add turn separators or grouping bands without changing block order.
5. Do not render labels inside every tile. Use an accessible name, tooltip, and inspector.
6. Hide zero-token blocks by default but allow the user to reveal them.

### Proportional mode

1. Replace the current square-root flex weighting with a true token-linear layout.
2. Implement a pure layout function that converts ordered blocks into fixed-capacity rows.
3. Split a block into visual segments when it crosses a row boundary. All segments select the same source block.
4. Keep exact area/length proportional to `token_count`; render zero-token blocks as markers outside the proportional calculation.
5. Overlay a larger transparent hit target for extremely narrow segments without changing their visible width.
6. Test token conservation, ordering, row capacity, oversized blocks, zero-token blocks, and empty input in the layout utility.

### Inspector and controls

1. Render the selected block in a persistent right inspector at wide widths and below the map on narrower widths.
2. Show block type, token count, message index, tool name, first-seen request, linked-block actions, and content state.
3. Constrain code/content to the inspector with `overflow: auto`, `white-space: pre-wrap`, and `overflow-wrap: anywhere` so long strings cannot enlarge the page.
4. Preserve links to previous message, tool call, and tool definition. Linked navigation should select the target and bring its tile/segment into view without switching to an enormous row list.
5. Add filters for block type, search, grouping, hide-zero, and jump-to-largest.
6. Use roving tabindex or equivalent arrow-key navigation across the block map. Add Escape to clear selection.
7. Keep Raw as a separate view. If the existing full parsed list remains available, label it Details and load it only on demand.

### Performance change

Remove the automatic tokenization of every block from the initial `ParsedViewer` load. Build both maps from server-provided token counts. Tokenize only the selected block when token highlighting is enabled, and cache results by block ID for the life of the workbench.

### Exit criteria

- Compact mode renders one tile per visible block and preserves API order.
- Proportional mode is token-linear and never exceeds its container width.
- Selecting a block exposes its complete available details without expanding hundreds of inline rows.
- A request with at least 350 blocks remains responsive during initial render, filtering, and selection.
- Purged blocks remain visible in both maps and show a clear purged state in the inspector.

## Phase 4: replace tool donuts with a treemap

### Files

- Refactor `ui/src/components/ToolBreakdown.tsx`.
- Add `ui/src/components/ToolTreemap.tsx` if separation keeps the component clearer.
- Update `ui/src/pages/Dashboard.tsx` and `ui/src/pages/RequestDetail.tsx` to use the new combined tool-analysis component.

### Work

1. Use Recharts' existing treemap capability; avoid adding another charting dependency unless Recharts cannot meet accessibility or layout requirements.
2. Build one parent node per tool using `definition_tokens + result_tokens`.
3. Subdivide each tool into Definitions and Results using related shades.
4. Assign the base color from a stable hash of `tool_name`, not the tool's current array index.
5. Group tools below a documented share threshold, initially 1%, into Other. The tooltip must still disclose the grouped total.
6. Render labels only when rectangles have enough space; always expose tool name, category, token count, and percentage through tooltip and accessible text.
7. Keep the sortable table as an exact-value alternative, initially showing the top tools with a Show all control.
8. Remove the long three-column legend and the two donut charts.

### Exit criteria

- Rectangle area tracks total tokens and Definitions/Results are visually distinguishable.
- A large long tail of tools remains legible and contained.
- Tool colors remain stable when values or sort order change.
- Exact data is still available without relying on color or area estimation.

## Phase 5: make request lists responsive

### Files

- Refactor `ui/src/components/RequestTable.tsx`.
- Update `ui/src/pages/Requests.tsx`.
- Update request-table use in `ui/src/pages/Dashboard.tsx` and `ui/src/pages/SessionDetail.tsx`.
- Adjust `ui/src/components/ContextBar.tsx` if necessary.

### Work

1. Replace the eleven-column default with primary columns for Time, Input tokens, Context summary, Duration, Status, and Model/Source.
2. Consolidate provider, agent, session, and model into a Source cell or expandable details row.
3. Move output and thinking totals into the expanded row or a compact stacked value.
4. Remove the unconditional 256px Context minimum width. Give the visualization a responsive minimum only when enough space exists.
5. At tablet/mobile widths, switch to request cards rather than compressing the table indefinitely.
6. Keep sorting on primary fields and expose `aria-sort` on sortable headers.
7. Give the search field and select controls explicit accessible labels.
8. If wide-table scrolling is still needed for an optional dense mode, constrain it to the table panel and add an obvious overflow cue.

### Exit criteria

- Agent and Model information is reachable at 1240px without page-level clipping.
- The request list is usable without horizontal scrolling at 768px and 390px.
- Sorting, filtering, pagination, and row navigation retain existing behavior.

## Phase 6: finish the visual migration

### Files

- Update `ui/src/pages/Dashboard.tsx`, `ui/src/pages/Requests.tsx`, `ui/src/pages/Sessions.tsx`, `ui/src/pages/SessionDetail.tsx`, and `ui/src/pages/Settings.tsx`.
- Update `ui/src/components/TokenDonut.tsx`, status badges, session controls, and shared empty/loading/error states.

### Work

1. Apply the semantic surface, border, typography, spacing, badge, and focus styles to every route.
2. Make `TokenDonut` stack its chart and value list inside narrow panels, or replace it with a more responsive category chart if testing shows the donut remains hard to scan.
3. Reduce duplicated category presentation on Request detail; one visual plus an exact table/disclosure is sufficient.
4. Standardize loading skeletons, empty states, warnings, and errors.
5. Preserve compact data density while increasing hierarchy through spacing, typography, and containment rather than adding more cards.
6. Verify that all block-type colors and status colors have accessible textual equivalents.

## Testing and verification

### Automated tests

Add Vitest, React Testing Library, and `@testing-library/user-event` to the UI test setup. Add tests for:

- `blockLayout.ts`: ordering, proportionality, token conservation, wrapping, oversized blocks, zero values, and empty data.
- Compact map: one tile per visible block, filtering, selection, keyboard movement, and accessible names.
- Inspector: normal, linked, purged, missing-content, and token-highlight states.
- Workbench: default Request view, Request/Response switching, Raw view, and preserved selection.
- Treemap data transformation: stable colors, Definitions/Results split, and Other grouping.
- Request list: responsive detail disclosure, sorting semantics, filters, and row navigation.

### Build and manual QA

1. Run `npm run build` from `ui/` after each phase.
2. Test the known crowded request:
   - `/requests/d02f9962-6afc-43f0-8e6d-4682b0f6aa37`
3. Test the Overview and All Requests routes with current production-like data.
4. Capture visual-regression screenshots at 1440×900, 1240×720, 1024×768, 768×800, and 390×844.
5. At every viewport, assert that `document.documentElement.scrollWidth <= document.documentElement.clientWidth`.
6. Test mouse, keyboard-only, 200% zoom, light-mode contrast, long tool names, long unbroken content, empty data, loading, API error, opaque context, and purged content.

## Recommended delivery sequence

Implement as three reviewable changes:

1. **Responsive foundation:** theme tokens, shell breakpoints, overflow fixes, and responsive request list.
2. **Request workbench:** reordered Request detail, compact/proportional maps, inspector, lazy tokenization, and accessibility.
3. **Analytics and polish:** tool treemap, dashboard migration, remaining light-theme work, and visual-regression QA.

Each change should keep the UI buildable and usable on its own. Do not mix backend schema changes into these changes unless implementation reveals missing data that cannot be derived from `RequestBlock` and `ToolStat`.

## Definition of done

- Request composition is visible in the first viewport on a standard laptop.
- Compact and proportional modes answer distinct questions and preserve request order.
- The proportional view uses actual token-linear sizing rather than square-root weighting.
- Block details are inspected without inline page growth or disruptive scrolling.
- Tool composition uses a readable treemap with an exact-value alternative.
- No route produces document-level horizontal overflow at the tested breakpoints.
- The application uses the light pastel design system consistently.
- Interactive elements are keyboard operable, visibly focused, and correctly named.
- Existing request, response, sorting, filtering, pagination, linkage, purged-content, and capture-state behavior is preserved.
- Automated UI tests and `npm run build` pass.
