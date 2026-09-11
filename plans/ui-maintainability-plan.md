# UI maintainability and simplification plan

> **Status:** Proposed
>
> **Scope:** Simplify the current UI implementation after the UI revamp without materially changing the shipped user experience. Functional or visual changes should be limited to fixing inconsistencies exposed by the refactor.

## 1. Goals

1. Remove obsolete UI implementations and compatibility code.
2. Make the content viewer understandable as a set of small, cohesive features rather than one large component.
3. Establish one source of truth for block arrangement, selection, visual styling, formatting, and token-window metadata.
4. Make additional content formats easier and safer to support.
5. Replace implementation-detail tests with tests of public behavior and pure transformations.
6. Add guardrails that prevent dead code and accidental duplication from accumulating again.

## 2. Non-goals

- Do not redesign the request-detail screen again.
- Do not change the established pastel block palette, semantic theme variables, or responsive shell.
- Do not replace Tailwind or introduce a second styling system.
- Do not alter request capture, token accounting, or persisted data.
- Do not add a large formatter dependency before bundle cost and browser behavior are evaluated.
- Do not combine components merely to reduce file count; extraction should follow clear responsibilities.

## 3. Guiding constraints

- Preserve request and response block ordering unless the user explicitly selects another arrangement.
- Preserve selection, linked-block navigation, keyboard navigation, search, content modes, structured collapsing, and token-window movement.
- Keep server-provided `token_count` as the source for composition sizing and aggregate totals.
- Tokenize content only when token highlighting is requested.
- Keep purged or unavailable blocks visible when their structural metadata exists.
- Every phase should build and test independently so it can be reviewed or reverted without depending on later phases.

## 4. Target architecture

```text
RequestWorkbench
├── BlockToolbar
├── BlockMap
│   ├── CompactBlockMap
│   ├── ProportionalBlockMap
│   └── BlockTile
├── BlockInspector
└── SearchableContentViewer
    ├── ContentToolbar
    ├── PlainTextView
    ├── TokenHighlightView
    ├── JsonTreeView
    └── StructuredTextView

lib/
├── blockArrangement.ts
├── blockLayout.ts
├── blockVisuals.ts
├── content/
│   ├── types.ts
│   ├── registry.ts
│   ├── json.ts
│   ├── xml.ts
│   ├── toml.ts
│   ├── javascript.ts
│   └── python.ts
└── textRanges.ts
```

The top-level components should coordinate state and data. Parsing, formatting, range calculation, sorting, and layout should be pure functions where practical.

## 5. Phase 0: baseline and safety net

### Purpose

Record current behavior before removing or reorganizing code.

### Work

1. Run the existing UI unit tests, production build, and backend tests covering tokenization.
2. Capture representative fixtures for:
   - JSON request content.
   - Nested XML.
   - TOML.
   - JavaScript containing strings, templates, comments, and regular expressions.
   - Python containing multiline strings and significant indentation.
   - Large content exceeding the current highlighting window.
   - Purged blocks and blocks with empty content.
3. Add one request-workbench interaction test covering:
   - Changing block filters.
   - Changing arrangement.
   - Selecting a block.
   - Switching content modes.
   - Searching content.
   - Expanding and collapsing structured content.
4. Record the current UI bundle size so dependency or extraction changes can be evaluated objectively.

### Exit criteria

- Existing behavior has regression coverage at the public component level.
- The production build and existing tests are green.
- Bundle size and representative content fixtures are recorded.

## 6. Phase 1: remove obsolete viewers

### Files

- Remove `ui/src/components/RawViewer.tsx`.
- Remove `ui/src/components/ParsedViewer.tsx`.
- Remove their compatibility styles from `ui/src/index.css`.
- Remove any tests, exports, comments, or documentation referring to those components.

### Work

1. Confirm neither component is imported by the application entry point, routes, tests, or package exports.
2. Delete both components rather than leaving forwarding wrappers.
3. Remove only the CSS selectors that existed for the legacy viewers; retain semantic theme tokens and shared request styles.
4. Search for old viewer terminology such as `RawViewer`, `ParsedViewer`, and legacy response-tab state.
5. Build and run the request-detail tests.

### Exit criteria

- The current request workbench behaves identically.
- No legacy viewer identifiers remain.
- Approximately 600 lines of unused TypeScript and related compatibility CSS are removed.

## 7. Phase 2: extract shared content-viewer primitives

### Purpose

Reduce duplication before splitting the large component, so the split does not copy existing logic into more files.

### Files

- Add `ui/src/lib/textRanges.ts`.
- Add `ui/src/components/ui/content-viewer/ContentToolbar.tsx`.
- Add focused tests for range composition and toolbar behavior.

### Work

1. Define a common range representation:

   ```ts
   interface TextRange {
     start: number
     end: number
     kind: 'plain' | 'syntax' | 'token' | 'search' | 'active-search'
     className?: string
   }
   ```

2. Implement a pure function that overlays search matches onto syntax or token spans while preserving source order and text exactly.
3. Replace the duplicated splitting logic in `HighlightedText`, `JsonSearchText`, `SyntaxHighlightedLine`, and `TokenHighlightedText`.
4. Move mode selection, search input, match navigation, token-window status, and structured expand/collapse controls into `ContentToolbar`.
5. Keep state ownership in `SearchableContentViewer` during this phase; the toolbar receives values and callbacks only.

### Tests

- Overlapping base and search ranges.
- Active versus inactive matches.
- Matches spanning syntax-token boundaries.
- Empty content and no matches.
- Unicode and multiline content.
- Output reconstruction equals the original source exactly.

### Exit criteria

- Text decoration is implemented once.
- Search appearance and navigation do not change.
- The extracted toolbar contains no parsing or content-rendering logic.

## 8. Phase 3: split `SearchableContentViewer`

### Files

- Keep `ui/src/components/ui/SearchableContentViewer.tsx` as the public coordinator.
- Add under `ui/src/components/ui/content-viewer/`:
  - `PlainTextView.tsx`
  - `TokenHighlightView.tsx`
  - `JsonTreeView.tsx`
  - `StructuredTextView.tsx`
  - `useContentSearch.ts`
  - `useTokenWindow.ts`
  - `types.ts`

### Responsibility boundaries

#### `SearchableContentViewer`

- Select the active mode.
- Detect the content language through the formatter registry.
- Coordinate search and selected-match navigation.
- Pass content and mode-specific state to one renderer.
- Contain loading and error boundaries.

#### Renderer components

- Render one content representation.
- Do not fetch unrelated data.
- Do not know about request blocks, treemaps, or workbench arrangement.
- Report searchable DOM targets through a small common interface.

#### Hooks

- `useContentSearch` owns query, matches, active index, next/previous movement, and scroll-to-match behavior.
- `useTokenWindow` owns request status, returned window metadata, retry, and move-to-visible-content behavior.

### Migration steps

1. Extract `PlainTextView` and its tests first.
2. Extract token highlighting behind the current API contract without changing behavior.
3. Extract the JSON tree.
4. Extract the generic structured-text tree.
5. Move orchestration-only state back into the reduced coordinator.
6. Keep the public props of `SearchableContentViewer` stable until all callers and tests are migrated.

### Exit criteria

- `SearchableContentViewer.tsx` is primarily coordination code and is preferably below 200 lines.
- Each renderer can be tested independently.
- No renderer duplicates search-range construction.
- Switching modes preserves the current query and predictable match selection.

## 9. Phase 4: replace collapse commands with controlled tree state

### Purpose

Remove local-node effects driven by `collapseAll` and `collapseRevision`.

### Files

- Add `ui/src/components/ui/content-viewer/useTreeExpansion.ts`.
- Update `JsonTreeView.tsx` and `StructuredTextView.tsx`.

### Work

1. Give every collapsible node a stable path derived from its parent path and property/index or line identity.
2. Store collapsed paths in one `Set<string>` per viewer.
3. Expose reducer actions:
   - `toggle(path)`
   - `expandAll()`
   - `collapseBelowDepth(1)` so the first two displayed levels remain open
   - `replaceValidPaths(paths)` when content changes
4. Derive the toolbar button label from actual expansion state rather than the last command issued.
5. Remove per-node effects and the revision counter.
6. Preserve individual node toggling after a global collapse or expand action.

### Tests

- Structured content begins expanded.
- Collapse keeps the first two displayed levels open.
- Expand restores all scopes.
- Manually toggling a node updates the derived toolbar state.
- Changing content discards obsolete paths without affecting valid ones.

### Exit criteria

- No `collapseRevision` or command-like prop remains.
- JSON and generic trees use the same expansion-state model.
- Expansion behavior is deterministic from parent state.

## 10. Phase 5: formalize token-window API behavior

### Purpose

Remove duplicated limits and fragile reconstruction from decoded token strings.

### Backend changes

- Update `contextspy/api/routers/tokenize.py`.
- Update `contextspy/analysis/tokenizer.py`.
- Update the API response model and backend tests.

### Frontend changes

- Update `ui/src/api/client.ts`.
- Update `useTokenWindow.ts` and `TokenHighlightView.tsx`.

### Proposed contract

Request:

```json
{
  "text": "...",
  "character_offset": 0,
  "max_tokens": 8000
}
```

Response:

```json
{
  "tokens": [
    { "text": "example", "start": 0, "end": 7 }
  ],
  "window_start": 0,
  "window_end": 50000,
  "total_characters": 120000,
  "truncated": true
}
```

Exact naming may follow the API's existing conventions, but the response must explicitly communicate the returned range and truncation.

### Work

1. Keep token and character limits in the backend only.
2. Return character spans or an equivalently reliable mapping; do not require the browser to infer the highlighted prefix by joining token strings.
3. Define behavior when the requested offset falls inside a multibyte character, surrogate pair, or token.
4. Maintain a temporary compatibility parser in the frontend only if mixed backend/frontend deployments must be supported. Remove it after the compatibility window.
5. Cache token windows by content identity, language/model if relevant, and returned range.
6. Make the “highlighted window” control use server-provided start/end values.
7. Add request cancellation so stale tokenization responses cannot replace the currently selected block.

### Tests

- Short content returns one complete window.
- Large content reports truncation and exact boundaries.
- Moving the window includes the requested location.
- Unicode content reconstructs correctly.
- Stale requests are ignored after mode or selected block changes.
- Backend limits cannot be bypassed by client parameters.

### Exit criteria

- No frontend copy of the server's character limit remains.
- The frontend performs no substring search to locate tokenized content.
- Window status is derived entirely from API metadata.

## 11. Phase 6: modularize formatting and highlighting

### Purpose

Make supported languages explicit and prevent the central helper from growing with every new format.

### Files

- Replace the monolithic responsibilities in `ui/src/lib/searchableContent.ts` with modules under `ui/src/lib/content/`.
- Retain a temporary re-export file only while imports are migrated.

### Interface

```ts
interface ContentLanguageAdapter {
  id: ContentLanguageId
  label: string
  detect(content: string): DetectionResult
  format?(content: string): FormatResult
  highlight(content: string): SyntaxSpan[]
  structure?(content: string): StructuredNode[]
}
```

`FormatResult` should distinguish success, unsupported content, and parse failure. It should also state whether the operation is guaranteed to preserve meaning.

### Work

1. Extract JSON first because it has the strongest parser and structure model.
2. Extract XML and add fixtures with mixed content and significant whitespace.
3. Extract TOML, JavaScript, and Python.
4. Make detection return a confidence or reason so ambiguous plain text does not get formatted incorrectly.
5. Create a registry responsible for detection order and adapter lookup.
6. Ensure “Verbatim raw” never modifies content.
7. Define “Formatted raw” carefully:
   - Use parser-backed formatting where it is demonstrably safe.
   - Fall back to layout-preserving display when formatting is unsupported or parsing fails.
   - Surface the detected language without presenting a guess as certain.
8. Evaluate mature formatter/highlighter libraries separately. If adopted, lazy-load them by mode/language and enforce a bundle-size budget.
9. Delete the compatibility re-export after all imports move to the registry.

### Tests

- Detection fixtures for every supported language and plain text.
- Formatting is deterministic and idempotent.
- Verbatim mode reproduces input byte-for-byte at the JavaScript string level.
- XML mixed-content whitespace is preserved.
- JavaScript templates, regular expressions, and comments are not corrupted.
- Python multiline strings and indentation remain valid.
- Invalid input produces a readable fallback rather than an exception.

### Exit criteria

- Adding a language does not require editing the viewer component.
- Detection, formatting, highlighting, and structure generation have separate tests.
- Unsupported or invalid content always has a safe raw fallback.

## 12. Phase 7: centralize block arrangement

### Purpose

Stop treating “size” as both a grouping and sorting mode and remove sorting from individual layouts.

### Files

- Add `ui/src/lib/blockArrangement.ts`.
- Update `ui/src/components/request/BlockToolbar.tsx`.
- Update `ui/src/components/request/RequestWorkbench.tsx`.
- Simplify `ui/src/lib/blockLayout.ts`.
- Update compact and proportional map tests.

### Data model

Prefer explicit state:

```ts
interface BlockArrangement {
  grouping: 'sequence' | 'turn' | 'toolPair'
  sorting: 'requestOrder' | 'largestFirst'
}
```

If the UI remains a single selector, expose named presets while keeping the internal model explicit.

### Work

1. Implement one pure `arrangeBlocks` pipeline:
   - Apply direction and block-type filters.
   - Form group metadata.
   - Apply stable ordering.
   - Return blocks plus group boundaries.
2. Preserve API/position order as the tie-breaker when token counts are equal.
3. Pass arranged blocks to both block maps.
4. Remove size sorting from `blockLayout.ts` and map components.
5. Describe response capabilities declaratively, for example `supportsGrouping`, instead of scattering special cases through the workbench.
6. Rename the selector to “Arrange” if it continues to include both grouping and size ordering.

### Tests

- Sequence order follows block position.
- Turn and tool-pair group boundaries are stable.
- Largest-first ordering is descending and stable for ties.
- Filters and token totals remain correct for request and response directions.
- Layout functions preserve caller-provided order.

### Exit criteria

- Block ordering is implemented in exactly one module.
- Maps perform layout and interaction, not business sorting.
- UI labels accurately describe whether a choice groups or sorts blocks.

## 13. Phase 8: share block tile behavior

### Files

- Add `ui/src/components/request/BlockTile.tsx`.
- Update `CompactBlockMap.tsx` and `ProportionalBlockMap.tsx`.
- Optionally add `useRovingBlockFocus.ts` if keyboard logic remains duplicated after tile extraction.

### Work

1. Move shared presentation and interaction into `BlockTile`:
   - Visual lookup and colors.
   - One-pixel default border.
   - Selected border treatment without changing fill shade.
   - Accessible name and selection state.
   - Click and focus behavior.
   - Purged-content indication.
2. Allow maps to supply geometry, internal labels, and neighboring-border behavior.
3. Keep grid-specific and proportional-map-specific navigation in their respective map components unless a genuinely common focus model emerges.
4. Avoid a prop-heavy universal component. If compact and proportional tiles diverge substantially, share a hook and visual utility instead of forcing all behavior into JSX conditionals.

### Exit criteria

- Selection, border, color, and accessibility behavior are defined once.
- Compact and proportional maps retain clear layout-specific code.
- Treemap tiles remain separate because they represent tool analytics, not request blocks.

## 14. Phase 9: simplify workbench derived state and navigation

### Files

- Update `RequestWorkbench.tsx`.
- Add `ui/src/lib/requestWorkbenchModel.ts` or a focused hook only if it reduces component responsibility.

### Work

1. Compute block collections, available filters, token totals, and visible arranged blocks through stable memoized selectors.
2. Avoid memoizing from arrays recreated immediately before the memo.
3. Extract DOM focus-and-scroll scheduling into one helper or hook with an explicit alignment policy.
4. Centralize selection transitions:
   - Clear selection when the selected block is filtered out.
   - Preserve selection when only the view changes.
   - Select and reveal linked blocks predictably.
5. Consider a reducer only if related state transitions become clearer. Do not replace several independent setters with a reducer that merely renames them.

### Exit criteria

- The component reads top-to-bottom as data derivation, event handlers, and rendering.
- Selection and focus behavior is covered by interaction tests.
- No duplicated nested animation-frame sequence remains.

## 15. Phase 10: consolidate repeated analytics composition

### Files

- Add `ui/src/components/ToolBreakdownSection.tsx` or rename the current composition component.
- Update Dashboard, Session detail, and Request detail callers.
- Review `ToolTreemap.tsx` exports and tests.

### Work

1. Combine the chart/treemap and exact-value table behind one public section component when they are always rendered together.
2. Put empty-state, total-token, responsive-layout, and “show all” behavior in that section.
3. Keep the treemap's pure data transformation separately testable.
4. Prefer testing public `ToolTreemap` behavior. Avoid exporting constants solely so tests can compare literal border strings.
5. Keep internal rendering helpers private unless they are intentionally reusable application APIs.

### Exit criteria

- Pages pass tool data and context totals once.
- Production exports reflect reusable APIs rather than test access requirements.
- Treemap interaction and selected status-line behavior remain covered.

## 16. Phase 11: consolidate small shared UI utilities

### Work

1. Move repeated duration, token-count, timestamp, and percentage formatting into `ui/src/lib/format.ts`.
2. Introduce a shared sortable header only if the request, session, and tool tables can use one small contract without numerous mode flags.
3. Extract repeated request summary fields shared by desktop and mobile only if doing so leaves the responsive markup easier to read.
4. Keep one-off disclosures and small local helpers colocated with their only caller.

### Rule

An extraction should either remove meaningful duplication, isolate a testable policy, or provide a stable shared visual behavior. Do not create a utility solely because a function is short.

### Exit criteria

- Formatting of the same value is consistent across screens.
- Shared components have at least two real callers.
- Responsive page markup remains understandable without following many trivial component indirections.

## 17. Phase 12: add maintainability guardrails

### Files

- Update `ui/tsconfig.json`.
- Update `ui/package.json` and CI configuration.
- Add lint or unused-export configuration.

### Work

1. Enable `noUnusedLocals` and `noUnusedParameters`, addressing findings in a dedicated change rather than mixing them into feature refactors.
2. Add ESLint rules for TypeScript and React hooks if equivalent checks are not already run elsewhere.
3. Evaluate an unused-export checker such as Knip. Configure intentional entry points explicitly rather than suppressing broad directories.
4. Add commands for:
   - Type checking.
   - Unit tests.
   - Production build.
   - Lint and unused-code checks.
5. Run these checks in CI.
6. Document the content-language adapter contract and block-arrangement invariants close to the relevant modules.

### Exit criteria

- An unused component like the former `RawViewer` fails an automated check.
- Hook dependency errors and unused declarations are reported before merge.
- Local and CI commands use the same configuration.

## 18. Test strategy after refactoring

### Prefer pure tests for

- Range overlay and reconstruction.
- Language detection and formatting.
- Structured-node creation.
- Block arrangement and proportional layout.
- Treemap data transformation.
- Token-window boundary calculation on the backend.

### Prefer component interaction tests for

- Selecting and navigating blocks.
- Switching content modes.
- Search next/previous behavior.
- Moving the token-highlight window.
- Expanding and collapsing structured scopes.
- Treemap selection and status-line updates.
- Request/response capability differences.

### Avoid

- Assertions against private component names.
- Exporting constants only to test exact class strings.
- Large snapshots of highlighted or formatted markup.
- Repeating pure formatter cases through slow page-level tests.

## 19. Delivery sequence

Implement as small reviewable changes in this order:

1. Baseline interaction tests and bundle measurement.
2. Remove the legacy viewers and compatibility CSS.
3. Extract range rendering and the content toolbar.
4. Split the content viewer.
5. Replace collapse-command state.
6. Add explicit token-window API metadata.
7. Introduce the content-language adapter registry.
8. Centralize block arrangement.
9. Extract shared request block behavior.
10. Simplify workbench state and navigation.
11. Consolidate tool analytics and small formatting utilities.
12. Enable lint, unused-code checks, and CI enforcement.

Avoid combining the token API migration, language adapter rewrite, and block-map refactor in one change. They have different failure modes and should be reviewable independently.

## 20. Definition of done

The maintainability work is complete when:

- Legacy request viewers and their CSS are gone.
- `SearchableContentViewer` is a coordinator over independently tested renderers.
- Search highlighting uses one shared range-composition implementation.
- Tree expansion is controlled through stable node paths without revision commands.
- Token-window limits and offsets come from explicit API metadata.
- Language support is registered through adapters and has safe fallbacks.
- Block sorting/grouping has one source of truth.
- Compact and proportional maps share selection and block styling behavior.
- Repeated tool analytics and value formatting are consolidated where beneficial.
- Type checking, linting, tests, production build, and unused-code detection pass in CI.
- The request-detail workflow and recent visual behavior remain unchanged except for documented bug fixes.

## 21. Expected result

The first phase should remove roughly 600 lines immediately. Later phases may not reduce the raw line count substantially because tests and explicit adapters add structure, but they should reduce the amount of code a developer must understand to change any one behavior. The primary measure of success is narrower responsibility and fewer sources of truth, not the smallest possible repository.
