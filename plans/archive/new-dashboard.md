# Active-session dashboard implementation specification

> **Status:** Proposed
>
> **Scope:** Add a live active-session section to the existing Overview page without redesigning or reordering the rest of the dashboard.

## 1. Goal

Make the Overview page immediately useful while a capture session is running. Directly after the existing four summary cards, add a live section that answers four questions:

1. Which session is active, how long has it been active, and how much traffic has it captured?
2. What were the most recent requests?
3. How are input and output token volumes changing request by request?
4. How large is the latest input context, and how did its token and block composition change from the preceding request?

The existing page layout and existing sections remain intact. In particular, the global summary cards, Token composition, Sessions, tool breakdown, model/latency panels, and full Recent requests table are not redesigned or removed.

## 2. Product decisions already made

- Insert the new section in `ui/src/pages/Dashboard.tsx` after the four summary cards and before the current Token composition / Sessions row.
- Remove `SessionControls` from the Overview title row. Session start/end controls and the active session name move into the new active-session panel.
- Do not change the controls on the dedicated Sessions page.
- Match the current Request detail visual system: existing semantic CSS variables, `panel`, `app-button*`, `app-badge`, typography, borders, radii, light theme, and dark theme.
- The request-flow row is newest-first, left to right.
- Request cards are compact and do not display HTTP method or endpoint URL.
- Each request card shows its session sequence number, age/time, model and latency when available, and input/output token totals.
- The activity visualization uses grouped bars, not lines.
- Input and output bars use separate Y axes because their magnitudes differ substantially.
- The Context panel does not show a model context-window limit or percentage. ContextSpy cannot reliably derive that limit without a model registry or manual configuration.
- The Context panel instead shows:
  - latest request input-context size;
  - signed token change from the preceding request in the same session;
  - signed changes in input block counts, such as tool calls, user messages, and tool results.

## 3. Non-goals

- Do not redesign the application shell, navigation, request-detail page, session-detail page, or other dashboard sections.
- Do not remove the existing global Recent requests table.
- Do not add or maintain a model-to-context-limit registry.
- Do not add settings for context limits.
- Do not calculate request analysis, block counts, token deltas, or comparison semantics in React.
- Do not persist dashboard-specific aggregates or deltas.
- Do not add a database column or data migration unless implementation uncovers missing source data. The current `requests`, `blocks`, and `sessions` tables are sufficient.
- Do not interpret a block-count delta as an exact content diff. This feature compares structural counts by block type, not content hashes or semantic meaning.

## 4. Placement on the existing Overview page

The final high-level order in `Dashboard.tsx` must be:

1. Overview heading, without the current top-right `SessionControls`.
2. Existing four summary cards, unchanged.
3. **New `LiveSessionSection`.**
4. Existing Token composition / Sessions row, unchanged.
5. Existing tool breakdown, unchanged.
6. Existing Models / Latency & Errors row, unchanged.
7. Existing full Recent requests table, unchanged.

The new section contains:

1. `ActiveSessionPanel`, full width.
2. `RequestFlow`, full width.
3. A two-column row with `RequestActivityChart` on the left and `ContextChangePanel` on the right.

At narrow widths the last row stacks, with the chart before the context-change panel. The request flow may scroll horizontally within its own region, but it must never create document-level horizontal overflow.

## 5. Data scope and semantics

All data in the new section is scoped to the single active session.

- If no session is active, do not silently fall back to global requests.
- If an active session has no requests, show the active-session panel plus explicit empty states for request flow, activity, and context change.
- Requests from earlier, ended, or ungrouped sessions must never appear in this section.

### 5.1 Request ordering

- Request flow: newest-first by `session_seq DESC`, with timestamp as a deterministic fallback.
- Activity chart: oldest-to-newest so time progresses conventionally from left to right.
- Context comparison: the latest request versus the immediately preceding request in the same active session.
- “Previous request” means the row with the greatest `session_seq` lower than the latest request. It does not mean the previous request globally and does not follow `predecessor_response_id` lineage.
- For legacy or exceptional rows with a null `session_seq`, fall back to `(timestamp, id)` ordering. The API should still return `session_seq: null`, and the UI should use a stable short request ID instead of inventing a sequence number.

### 5.2 Token values

- “Input” and “Context size” use `Request.tokens_total_input`, matching the block-composition analysis and existing Context figures in the UI.
- “Output” uses `Request.tokens_total_output`.
- Session totals are sums of those fields across requests in the active session.
- `provider_input_tokens` is not substituted for `tokens_total_input`; it may use provider-specific accounting and may not reconcile with classified blocks.
- Token delta is:

```text
latest.tokens_total_input - previous.tokens_total_input
```

- Positive, zero, and negative deltas are all valid. Growth must not be styled as inherently successful; use the accent color plus an explicit sign/arrow, not success green.
- For the first request in a session, token delta is `null` and the UI says “First request in session.”

### 5.3 Block changes

Block changes compare persisted `BlockRecord` rows for the latest and previous requests.

- Count only `direction == "input"` blocks. Output blocks are excluded because this panel describes the next request's input context.
- Group counts by `block_type` using the provider-independent `BlockType` values from `contextspy/analysis/blocks.py`.
- For each type:

```text
delta = current_count - previous_count
```

- Include current and previous counts in the API response even though the first UI only displays the signed delta. This keeps the contract testable and supports future detail views without changing comparison semantics.
- Count block rows even when their content has been purged or their token count is zero. The structural metadata remains available and is the intended source.
- This is a net structural-count comparison. A zero delta does not prove the contents are identical; two same-type blocks may have been replaced. The UI must label the section “Block changes,” not “New blocks” or “Content diff.”
- Block comparison fidelity is:
  - `complete` when both requests have complete context;
  - `partial` when either request is partial but neither is opaque;
  - `unavailable` when there is no previous request, either request is opaque, or block records are unavailable.
- Token delta remains available when block comparison is unavailable.

The initial UI displays non-zero changes in this priority order:

1. `tool_call` → “Tool calls”
2. `user_message` → “User messages”
3. `tool_result` → “Tool results”
4. `assistant_message` → “Assistant messages”
5. `tool_definition` → “Tool definitions”
6. `system_prompt` → “System prompts”
7. `assistant_prefill` → “Assistant prefills”
8. `thinking` → “Thinking blocks”
9. `other` → “Other blocks”

Show up to four rows. If additional non-zero types exist, show a final “N other block types changed” row. This selection and label mapping are presentation concerns; the backend returns every compared type.

## 6. Backend API

### 6.1 New endpoint

Add:

```http
GET /api/stats/dashboard-live
```

Implementation locations:

- Route: `contextspy/api/routers/stats.py`
- Query/comparison logic: `contextspy/db/crud.py`

Suggested CRUD function:

```python
def get_dashboard_live(db: OrmSession) -> dict:
    ...
```

Do not build this payload in the router. The router should only open the database session and return the CRUD result.

### 6.2 Response contract

Example with an active session and at least two requests:

```json
{
  "active_session": {
    "id": "session-uuid",
    "name": "Checkout latency investigation",
    "started_at": "2026-09-18T08:18:00+00:00",
    "request_count": 18,
    "tokens_total_input": 318420,
    "tokens_total_output": 5921
  },
  "request_flow": [
    {
      "id": "request-18",
      "session_seq": 18,
      "timestamp": "2026-09-18T08:42:08+00:00",
      "model": "gpt-5.2",
      "duration_ms": 1800,
      "status_code": 200,
      "invocation_outcome": "completed",
      "tokens_total_input": 87412,
      "tokens_total_output": 612
    }
  ],
  "activity": [
    {
      "id": "request-9",
      "session_seq": 9,
      "timestamp": "2026-09-18T08:29:10+00:00",
      "tokens_total_input": 42000,
      "tokens_total_output": 340
    }
  ],
  "context_change": {
    "request_id": "request-18",
    "session_seq": 18,
    "tokens_total_input": 87412,
    "previous_request_id": "request-17",
    "previous_session_seq": 17,
    "token_delta": 5612,
    "comparison_fidelity": "complete",
    "block_changes": [
      {
        "block_type": "tool_call",
        "current_count": 14,
        "previous_count": 12,
        "delta": 2
      },
      {
        "block_type": "user_message",
        "current_count": 9,
        "previous_count": 8,
        "delta": 1
      },
      {
        "block_type": "tool_result",
        "current_count": 13,
        "previous_count": 11,
        "delta": 2
      }
    ]
  }
}
```

No active session:

```json
{
  "active_session": null,
  "request_flow": [],
  "activity": [],
  "context_change": null
}
```

Active session with no requests uses a non-null `active_session`, zero totals, empty arrays, and `context_change: null`.

Active session with exactly one request returns that request in both request arrays and a `context_change` object with:

```json
{
  "previous_request_id": null,
  "previous_session_seq": null,
  "token_delta": null,
  "comparison_fidelity": "unavailable",
  "block_changes": []
}
```

### 6.3 Query design

Keep the endpoint bounded and avoid loading the full session into Python.

1. Fetch the active session with `is_active == 1`.
2. If absent, return the empty contract immediately.
3. Aggregate request count, input tokens, and output tokens for that session in SQL.
4. Fetch at most the ten newest request rows for the active session.
5. Build `request_flow` from the newest five rows.
6. Build `activity` from all fetched rows reversed into chronological order.
7. Select the latest and previous rows from the same bounded result.
8. Count input blocks for those two request IDs in one grouped query:

```python
select(
    BlockRecord.request_id,
    BlockRecord.block_type,
    func.count().label("block_count"),
).where(
    BlockRecord.request_id.in_([latest.id, previous.id]),
    BlockRecord.direction == Direction.INPUT,
).group_by(BlockRecord.request_id, BlockRecord.block_type)
```

9. Build a union of the two type sets and return a row for every type in that union, including zero deltas. Returning zeros makes the contract complete; the UI decides which values are useful to display.

This should take a small fixed number of queries and must not perform a per-request or per-block N+1 query.

### 6.4 Schema and migrations

No schema change is expected.

- Session identity and start time already exist on `Session`.
- Per-request token totals and `session_seq` already exist on `Request`.
- Structural block type and direction already exist on `BlockRecord`.
- Purged content does not remove the block row.

If implementation does add a persisted field despite this plan, it must follow the migration rules in `CLAUDE.md`: update `contextspy/db/database.py:_migrate()` for additive columns and add a versioned data migration when old rows require a backfill.

## 7. Frontend API and live refresh

### 7.1 Types and client

In `ui/src/api/client.ts`, add explicit interfaces rather than reusing the full `Request` type:

- `DashboardActiveSession`
- `DashboardRequestFlowItem`
- `DashboardActivityPoint`
- `DashboardBlockChange`
- `DashboardContextChange`
- `DashboardLiveData`

Add:

```ts
statsApi.dashboardLive = () => apiFetch<DashboardLiveData>('/stats/dashboard-live')
```

Keep the payload lean. Raw bodies, category totals, context accounting, endpoints, agents, and response reconstruction metadata are not needed here.

### 7.2 Query hook

In `ui/src/api/hooks.ts`, add:

```ts
export function useDashboardLive() {
  return useQuery({
    queryKey: ['stats', 'dashboard-live'],
    queryFn: () => statsApi.dashboardLive(),
    refetchInterval: 5_000,
  })
}
```

The current WebSocket handler invalidates the `['stats']` prefix on `new_request`, `session_started`, and `session_ended`, so the new query will refresh immediately after live events without a new WebSocket event type.

Also update mutation invalidation for reliable non-WebSocket behavior:

- `useCreateSession`: invalidate `['sessions']` and `['stats']`.
- `useEndSession`: already invalidates both; retain that behavior.
- `useRenameSession`: invalidate `['sessions']`, `['session', id]`, and `['stats', 'dashboard-live']` so the active panel cannot retain an old name.

Do not insert received request data directly into the cache in this change. Invalidation keeps the endpoint as the single source of comparison truth.

## 8. Frontend components

Create focused components under `ui/src/components/dashboard/`:

```text
LiveSessionSection.tsx
ActiveSessionPanel.tsx
RequestFlow.tsx
RequestActivityChart.tsx
ContextChangePanel.tsx
```

Optionally add `dashboardFormat.ts` only for pure display helpers or block-type labels. Do not put aggregation or comparison logic there.

### 8.1 `LiveSessionSection`

Responsibilities:

- Call `useDashboardLive` once.
- Own the section-level loading, error, no-active-session, and active-session states.
- Pass server-provided data to child components without recomputing totals or deltas.
- Render the active panel, request flow, and two-column analytics row in the agreed order.
- Keep all failures non-fatal; the rest of the existing dashboard must still render if this endpoint fails.

Loading state:

- Render neutral panels with stable minimum heights so the page does not jump substantially.
- Use existing theme surfaces and muted text; do not add a second loading framework.

Error state:

- Render one compact `notice-warning` or panel message: “Live session data could not be loaded.”
- Do not hide or disable the rest of the Overview page.

### 8.2 `ActiveSessionPanel`

Active state displays:

- “Active session” plus the current success indicator.
- Session name as a link to `/sessions/{id}`.
- Elapsed time derived from `started_at` and the current clock.
- Session request count.
- Cumulative session input tokens.
- Cumulative session output tokens.
- `End session` action using `useEndSession`.

Elapsed time is presentation logic. A small hook may update `Date.now() - started_at` once per second while under one minute and once per minute afterward, or simply once per minute if a coarser display is chosen. Clear the timer on unmount and respect the existing `formatElapsedDuration` helper.

No-active state displays:

- “No active session.”
- A short explanation that a session groups live requests.
- `Start session` action using the existing create-session behavior.

Refactor `SessionControls.tsx` so the start-session dialog is reusable rather than duplicating its state and form markup. A recommended split is:

```text
ui/src/components/StartSessionDialog.tsx
ui/src/components/SessionControls.tsx
ui/src/components/dashboard/ActiveSessionPanel.tsx
```

`SessionControls` remains usable on the Sessions page. Only the Overview header stops rendering it.

Action details:

- Preserve trimmed, non-empty name validation.
- Preserve the backend warning behavior that starting a session ends any previous active session.
- Disable buttons while their mutations are pending.
- Show mutation errors near the action rather than failing silently.
- After ending the session, keep the panel mounted until query invalidation resolves, with the button disabled to prevent double submission.

### 8.3 `RequestFlow`

Render at most five API-provided items in response order. The first visual item must be the newest request.

Each card contains:

- `#<session_seq>`; use a short stable request ID when sequence is null.
- Relative age or local time.
- Model and formatted duration on one muted line when available.
- Input tokens with a down/in marker.
- Output tokens with an up/out marker.
- A visible status treatment only when the request failed or is incomplete; successful requests do not need an additional badge.

Explicitly omit:

- HTTP method;
- endpoint URL;
- provider and agent unless the model is missing and a fallback label is needed;
- category composition;
- long request IDs.

Interaction:

- Cards are native buttons or links and navigate to `/requests/{id}`.
- Keyboard focus uses the global focus-visible style.
- The accessible name includes sequence/fallback ID, timestamp, input tokens, output tokens, and status.
- Do not implement local card selection that changes the context panel; the context panel always represents the latest request.

Responsive behavior:

- Wide layouts: five equal compact cards.
- Narrow layouts: keep a horizontal newest-to-oldest strip using container-level `overflow-x-auto`; do not shrink text below current UI conventions and do not allow page-level overflow.
- Preserve DOM order so keyboard and screen-reader order also remains newest-first.

### 8.4 `RequestActivityChart`

Use the existing Recharts dependency and render a `BarChart`.

- X axis: session request sequence or fallback short ID.
- Left Y axis: input tokens.
- Right Y axis: output tokens.
- Bar 1: input tokens, bound to the left axis.
- Bar 2: output tokens, bound to the right axis.
- Data arrives oldest-to-newest from the API; do not reverse it in the component.
- Show up to ten requests.
- Use separate automatic domains. Do not normalize the values and do not put both series on one scale.
- Use compact token tick formatting (`1.2k`, `87k`, `120k`) and exact localized integers in the tooltip.
- Tooltip rows must say “Input” and “Output” and include units.
- Include visible or screen-reader axis labels so the two scales cannot be mistaken for one another.
- Include an accessible summary such as “Input and output token totals for the ten most recent requests; input uses the left axis and output uses the right axis.”
- Do not rely on color alone; series names and axis association must be explicit.

Add semantic chart variables to both theme palettes in `ui/src/index.css`, for example:

```css
--chart-input: ...;
--chart-output: ...;
```

Choose values from the existing ContextSpy palette. Do not introduce a disconnected blue/orange dashboard theme.

Recommended Recharts structure:

```tsx
<BarChart data={activity}>
  <CartesianGrid ... />
  <XAxis dataKey="label" ... />
  <YAxis yAxisId="input" orientation="left" ... />
  <YAxis yAxisId="output" orientation="right" ... />
  <Tooltip ... />
  <Bar yAxisId="input" dataKey="tokens_total_input" ... />
  <Bar yAxisId="output" dataKey="tokens_total_output" ... />
</BarChart>
```

Do not reuse the existing `TimeSeriesChart` directly: it is a line chart over time buckets and exposes only input tokens. Reuse small pure formatting helpers if useful, but keep its existing Session detail behavior unchanged.

### 8.5 `ContextChangePanel`

Header:

- Title: “Context size.”
- Subtitle: “Latest request · compared with #N,” or “First request in session.”

Primary value:

- Latest `tokens_total_input`, formatted compactly or with grouped digits according to available width.
- The word “tokens.”
- Signed token delta when present: `+5,612`, `−840`, or `0`.
- Use a directional glyph and text/sign together. Do not communicate direction through color alone.
- Use accent color for token delta regardless of sign, because context growth is not inherently good and context reduction is not inherently bad.

Block changes:

- Render non-zero `block_changes` in the priority order in section 5.3.
- Use explicit signs for positive and negative values.
- Positive structural additions may use `var(--success)` and removals may use `var(--danger)`, but the sign must remain visible.
- When all deltas are zero, show “No block-count changes.”
- When `comparison_fidelity == "partial"`, show a small muted “Partial capture” note.
- When comparison is unavailable but a previous request exists, show “Block comparison unavailable.”
- When there is no previous request, do not render an empty Block changes list.

Do not show:

- a progress bar;
- a percentage;
- a maximum context size;
- a guessed model limit;
- remaining capacity.

## 9. Styling requirements

Use the existing design system from `ui/src/index.css`.

- Ordinary containers use `panel`; do not create bespoke shadows or large radii.
- Use `section-title`, `eyebrow`, `app-button*`, `app-badge`, and semantic color variables where applicable.
- Preserve the current dense, quiet Request detail aesthetic.
- Use font weights and sizes already present in dashboard and request-detail components.
- Numbers use `tabular-nums`.
- Long session/model names truncate visually but remain available through a `title` or accessible label.
- Both themes must be supported by semantic tokens; no light-only hex values in components.
- Respect `prefers-reduced-motion`; the existing global rule already reduces animations.
- A live indicator may reuse the current small pulsing dot. It must also include the visible text “Active session.”

Suggested layout classes:

```text
Live section:             space-y-3 or space-y-4
Active session panel:     grid/flex with wrapping actions
Request flow:             five columns wide; horizontal strip narrow
Analytics row:            grid-cols-1 lg:grid-cols-[minmax(0,1.6fr)_minmax(240px,.4fr)]
```

Exact utility classes may change during implementation, but the hierarchy and responsive behavior are acceptance requirements.

## 10. Accessibility requirements

- All start/end/request actions use native buttons or links.
- The start-session dialog retains `role="dialog"`, `aria-modal="true"`, a labelled title, autofocus, Enter submission, and Escape cancellation.
- The request strip has a visible heading and an accessible description that it is ordered newest-first.
- Request cards have meaningful accessible names and visible focus.
- The activity chart has an accessible name/description and identifies which Y axis belongs to which series.
- Token and block deltas include visible signs; color is supplementary.
- Loading and mutation status that changes after an action uses an appropriate polite live region.
- Empty and error states are readable without chart interpretation.
- At 200% zoom, the section may stack or scroll internally but must not cause document-level horizontal overflow.

## 11. Backend tests

Add focused CRUD tests, preferably in `tests/test_dashboard_stats.py`. Construct an in-memory SQLite database with `Base.metadata.create_all(engine)` and insert sessions, requests, and blocks directly or through CRUD helpers.

Required cases:

1. No active session returns the exact empty contract.
2. Active session with no requests returns session metadata, zero totals, empty request arrays, and no context change.
3. Requests from inactive or ungrouped sessions are excluded.
4. Active-session totals sum input and output correctly.
5. Request flow contains at most five rows, newest-first.
6. Activity contains at most ten rows, oldest-to-newest.
7. Positive token delta is calculated correctly.
8. Negative token delta is preserved, not clamped.
9. First request returns a null delta and unavailable comparison.
10. Previous request is selected within the same session by `session_seq`, even when a newer global request belongs to another session.
11. Block counts include input rows and exclude output rows.
12. Block changes include positive, negative, and zero deltas with current and previous counts.
13. Purged block content does not affect structural counts.
14. Partial and opaque context produce the documented fidelity state.
15. Null `session_seq` rows use deterministic timestamp/ID ordering without crashing.

Where practical, add one router-level test for `/api/stats/dashboard-live` to ensure the route exposes the CRUD contract unchanged. Do not duplicate every CRUD case through HTTP.

## 12. Frontend tests

Add component tests under `ui/src/components/dashboard/` and an Overview integration test.

### `ActiveSessionPanel.test.tsx`

- Displays the active name, elapsed label, request count, and token totals.
- Session name navigates to Session detail.
- End action calls the mutation once and disables while pending.
- No-active state opens the shared start-session dialog.
- Long names remain accessible when visually truncated.

### `RequestFlow.test.tsx`

- Renders API order with the newest item first.
- Shows sequence, model/latency, and input/output values.
- Does not render the endpoint or HTTP method.
- Navigates to the correct request.
- Null sequence uses the documented fallback ID.
- Failure/incomplete state has accessible text.

### `RequestActivityChart.test.tsx`

- Receives chronological data without resorting it.
- Binds input and output bars to distinct Y-axis IDs.
- Formats large values without changing the underlying data.
- Tooltip exposes exact input and output values.
- Empty data renders the expected empty state.

If Recharts internals make DOM assertions brittle in JSDOM, extract only the pure label/tooltip formatting into tested helpers and keep one shallow render assertion for the chart. Do not test Recharts implementation details.

### `ContextChangePanel.test.tsx`

- Shows total context size and positive, negative, and zero token deltas.
- Shows the correct comparison sequence.
- Orders and labels block changes as specified.
- Omits zero block deltas.
- Handles more than four changed types with the “other” summary.
- Shows first-request, partial, and unavailable states.
- Never renders a context limit, percentage, or progress bar.

### `Dashboard.test.tsx`

- Mocks the existing dashboard hooks plus `useDashboardLive`.
- Verifies the active-session section appears after the four summary cards and before Token composition.
- Verifies the Overview header no longer contains `SessionControls`.
- Verifies existing dashboard sections still render.
- Verifies a live-section API failure does not suppress existing dashboard content.

### Live refresh tests

- Confirm `useCreateSession` invalidates both Sessions and Stats queries.
- Confirm `useRenameSession` invalidates the dashboard-live query.
- If a WebSocket test harness is added, confirm `new_request`, `session_started`, and `session_ended` invalidate the `['stats']` prefix; otherwise cover this by inspection and keep the existing handler unchanged.

## 13. Manual QA matrix

Test with realistic data in both light and dark themes.

### Data states

- No sessions exist.
- Sessions exist but none is active.
- Active session with zero requests.
- Active session with one request.
- Active session with 2–4 requests.
- Active session with more than 10 requests.
- Latest context grows substantially.
- Latest context shrinks.
- Output is much smaller than input.
- Input is tens or hundreds of thousands while output is hundreds or low thousands.
- Requests with zero tokens.
- Missing model, duration, status code, or session sequence.
- Partial and opaque captures.
- API loading, error, and slow-response states.
- Start/end mutation errors.

### Viewports and interaction

- 1440×900
- 1240×720
- 1024×768
- 768×800
- 390×844
- 320px wide
- Keyboard-only navigation
- 200% browser zoom
- Reduced-motion preference

At every width, verify:

```js
document.documentElement.scrollWidth <= document.documentElement.clientWidth
```

The request strip may have its own horizontal overflow at narrow widths; the page may not.

## 14. Implementation phases

### Phase 1: backend contract

Files:

- `contextspy/db/crud.py`
- `contextspy/api/routers/stats.py`
- `tests/test_dashboard_stats.py`

Work:

1. Add the bounded active-session aggregate query.
2. Add recent request summaries and deterministic ordering.
3. Add latest-versus-previous token comparison.
4. Add grouped input block-count comparison and fidelity state.
5. Expose `/stats/dashboard-live`.
6. Complete backend tests before beginning UI integration.

Exit criteria:

- The API contract in section 6 is stable and tested.
- All comparison logic lives in Python.
- No schema migration is introduced.

### Phase 2: frontend contract and query lifecycle

Files:

- `ui/src/api/client.ts`
- `ui/src/api/hooks.ts`
- hook tests as needed

Work:

1. Add the dashboard-specific types and API method.
2. Add `useDashboardLive`.
3. Update create/rename mutation invalidation.
4. Confirm existing WebSocket invalidation covers the new query key.

Exit criteria:

- The UI consumes one typed dashboard-live payload.
- No raw block aggregation or request comparison exists in TypeScript.

### Phase 3: reusable session-start behavior

Files:

- Extract `ui/src/components/StartSessionDialog.tsx`.
- Update `ui/src/components/SessionControls.tsx`.
- Add dialog/component tests.

Work:

1. Move the current modal and validation into a reusable component.
2. Keep Sessions-page behavior unchanged.
3. Expose an open/close interface usable by `ActiveSessionPanel`.

Exit criteria:

- There is one implementation of the start-session form.
- Existing session controls still work on the Sessions page.

### Phase 4: dashboard components

Files:

- Add the five components under `ui/src/components/dashboard/`.
- Update `ui/src/index.css` with input/output chart tokens if needed.
- Add component tests.

Work:

1. Implement active/no-active states.
2. Implement compact newest-first request flow.
3. Implement the dual-axis grouped bar chart.
4. Implement context total, signed token delta, and block changes.
5. Complete responsive and accessibility behavior.

Exit criteria:

- Each component handles loading/empty/missing-value cases without assumptions.
- The chart uses two independent axes.
- The context panel contains no limit or percentage.

### Phase 5: Overview integration and regression verification

Files:

- `ui/src/pages/Dashboard.tsx`
- `ui/src/pages/Dashboard.test.tsx`

Work:

1. Remove `SessionControls` from the Overview heading.
2. Insert `LiveSessionSection` after the summary cards.
3. Leave all following dashboard sections in their current order.
4. Add integration coverage and perform the manual QA matrix.

Exit criteria:

- The requested section occupies the agreed location.
- Existing Overview functionality remains available.
- Start/end/new-request changes appear without a manual page reload.

## 15. Verification commands

Backend changes require the backend suite:

```bash
pytest tests/test_dashboard_stats.py
pytest
```

Frontend changes require tests, type checking, linting, unused-code checks, and a production build:

```bash
cd ui
npm run check
```

Because `contextspy start` serves `contextspy/_web/`, run the UI build before testing the production command if `npm run check` was not run in the same working tree:

```bash
make ui
```

During active visual work, use `make dev-backend` and `make dev-ui` in separate terminals.

## 16. Definition of done

- Overview's existing layout is preserved except for moving its session controls into the new section.
- The new live section appears between the existing summary cards and Token composition.
- Active session name, elapsed time, request count, cumulative input/output, and end action are visible together.
- No-active state supports starting a session through the existing validated flow.
- Request flow shows up to five requests, newest on the left, with no method or endpoint text.
- Request cards navigate to Request detail and remain keyboard accessible.
- Activity shows up to ten requests as paired bars with independent input and output Y axes.
- Context size shows the latest input total and a signed token delta from the previous request.
- Block changes show signed input block-count deltas from the previous request.
- No model context limit, percentage, remaining-capacity estimate, or progress bar is present.
- Backend Python owns every aggregate and comparison decision.
- No database migration is required.
- WebSocket and mutation invalidation keep the section current.
- Light mode, dark mode, loading, error, empty, partial, and opaque states are handled.
- No tested viewport develops document-level horizontal overflow.
- Backend tests, `npm run check`, and the production UI build pass.
