# ContextSpy UI critique

## Summary

The main problem is information hierarchy, not merely styling. ContextSpy's core value is explaining what a model request contains, but the request composition is treated as secondary content. The data is useful; the interface currently optimizes for completeness rather than investigation.

The strongest redesign principle is **overview first, details on selection**. The current request page presents metadata and secondary analytics first and makes the structural overview something the user must find and expand.

## Request-detail information architecture

The request page should be reordered as follows:

1. Compact header: status, model, time, duration, context tokens, and output tokens.
2. Opaque-context or capture warning, when applicable.
3. Request composition workbench, visible immediately and open by default.
4. Secondary category and tool breakdowns.
5. Remaining request metadata.
6. Raw payload and other advanced diagnostic views.

Request and Response should be top-level tabs in the workbench instead of separate accordions at the bottom of the page. This would remove the current indirect interaction where the token cards scroll to and toggle distant content.

## Request composition

Use two deliberately different visualization modes because one view cannot make every block equal in importance while also encoding its token size accurately.

### Compact mode

- Represent every request element as an equal-sized square.
- Preserve request order from left to right and top to bottom.
- Use fill color for block type and a one-pixel near-black border to distinguish adjacent blocks.
- Show the label, exact token count, tool name, message index, and relationship links in a tooltip or inspector rather than attempting to label every tile.
- Add visual separators or grouping for turns and tool call/result pairs.
- Open a persistent inspector when a tile is selected, keeping the composition visible and the user's position stable.

### Proportional mode

- Use a wrapped token timeline in which horizontal length represents token count.
- Give each row a fixed token capacity so the visualization remains contained within the viewport.
- Preserve sequence across rows and split very large blocks across row boundaries when necessary.
- Give very small blocks a usable interaction target without pretending that the enlarged target is their true token share.
- Show exact values and block relationships in the inspector.

The current overview is visually dense because it attempts to label narrow segments. Its flex weighting uses the square root of token count, so block lengths are not genuinely proportional. The current Parsed view has the inverse problem: hundreds of blocks receive almost identical full-width rows, making individual items readable but the overall structure difficult to understand.

Useful controls above the workbench would include:

- Filters for System, User, Assistant, Thinking, Tool Call, and Tool Result.
- Grouping by turn or tool call/result pair.
- Hide zero-token elements, enabled by default.
- Search block content and tool names.
- Zoom controls and a "jump to largest block" action.
- A sticky legend showing visible block counts and token totals.

## Overflow and responsive behavior

The current interface has concrete containment problems:

- At a 1240px viewport, the All Requests table cuts off the Agent and Model columns.
- At 1024px, the request-detail Token composition card clips the legend's token values.
- At 768px, the fixed sidebar consumes too much space and the metadata panel becomes excessively tall.

The redesign should:

- Use responsive grids with `minmax()` and ensure flex/grid children have `min-width: 0`.
- Stack analytical cards when their internal visualization cannot shrink cleanly.
- Keep horizontal scrolling inside a specific table or code panel; the document itself must never scroll horizontally.
- Reduce the request table to the primary scanning fields: Time, Input, Duration, Status, and Model.
- Move Output, Thinking, Session, Provider, and Agent into an expandable row or a consolidated Source column.
- Collapse the sidebar to an icon rail or drawer at narrower desktop and tablet widths.
- Use a card/list presentation for requests on small screens.

## Tool composition

Replacing the tool donuts with a treemap is appropriate. The current combination of a long legend and two donuts requires repeated color matching and becomes difficult to use as the number of tools grows.

The treemap should:

- Allocate one rectangle per tool according to total token share.
- Subdivide each tool into Definitions and Results using related shades.
- Show labels only when sufficient space is available.
- Group very small entries under Other.
- Provide exact values and percentages in a tooltip and optional detail table.
- Use a stable color assignment so a tool does not change color when sorting changes.

A treemap is suitable for share-of-total analysis. It should not replace the ordered request composition because treemaps discard sequence.

## Visual design direction

A restrained, light analytical style would suit the product:

- Warm off-white canvas, such as `#F7F7F4`.
- White primary surfaces.
- Near-black primary text and one-pixel near-black borders for graphical blocks.
- Pastel lavender, blue, mint, peach, yellow, and rose block fills.
- Tabular numerals for token counts and latency values.
- Six-to-eight-pixel corner radii, with shadows reserved for overlays and the inspector.
- Less uppercase text and letter spacing in labels.
- A smaller logo/wordmark in the navigation; the mascot currently dominates the sidebar.

Color must not be the only indicator of meaning. Block types should retain explicit text labels, abbreviations, or icons, and status badges should always include readable text.

## Additional usability and accessibility findings

- The request search field relies on its placeholder and does not expose a clear accessible name; add a visible label or `aria-label`.
- Sorting should expose its current direction through `aria-sort`, not only a visual arrow.
- Tiles and block rows need visible keyboard focus, selection state, and keyboard navigation.
- Tool-call relationship actions need accessible names and consistent placement.
- Empty, purged, loading, and opaque-content states should remain clearly distinguishable.
- Avoid tokenizing every block on initial load. The compact overview can use server-provided token counts; token-level highlighting should load only for the selected block or when explicitly requested.

## Priority

- **P0:** Move request composition into the first viewport and fix clipping and page-level overflow.
- **P1:** Add Compact and Proportional composition modes with a persistent block inspector; simplify the request table.
- **P2:** Replace the tool donuts with a treemap and apply the light pastel design system.
- **P3:** Add filtering, grouping, keyboard navigation, and saved display preferences.
