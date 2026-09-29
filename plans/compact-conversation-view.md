# Compact conversation view

## Status and scope

Implemented on 2026-09-29. This document records the agreed UI behavior and the backend data used to support it. The implementation did not change lineage inference or the database schema.

The current dashboard shows a short request flow followed by separate conversation rows, then the activity chart and a context panel. Session Detail's Conversations view shows grouped rows and a group-level context panel, but no activity chart. Request cards navigate immediately. The goal is to make the common case a compact, single-row chronological view while retaining the existing conversation breakdown and lineage diagnostics.

## Product contract

### Two layouts, one set of requests

- Default to **Sequence**: one horizontally scrollable row of all requests in the session, newest to oldest. Each stored request appears once, including requests in Auxiliary requests and shared ancestry that appears in multiple grouped conversation rows. This is chronological capture order, not a claimed parent chain.
- Offer **Conversations**: the existing separate row per supported conversation, plus the Auxiliary requests row when present. Preserve conversation evidence, segment boundaries, segment index, and per-row pagination. These rows may repeat shared history; that is a property of the grouped view, not an increase in session request count.
- Put the layout switch at the right of the sequence header. When more than one display group exists, its Sequence-mode label is `N conversations`, where `N = conversation_count + (auxiliary exists ? 1 : 0)`. In Conversations mode the label is `Show sequence`. A session with only one group does not need a redundant switch in Sequence mode.
- Keep the separate **Fragments** diagnostic mode; it is not a third arrangement of the same conversation cards.
- Existing links with `?view=lineage&conversation=<key>` must open Conversations layout with that group in focus. A new explicit URL parameter such as `layout=conversations` can preserve a chosen layout on navigation; absent it, default to Sequence. Keep `mode=fragments` and unrelated query parameters intact.

### Compact and detailed cards

- Add a **Compact mode** toggle immediately left of the layout switch. Compact is enabled by default and stored in browser local storage so the setting follows the user between Dashboard and Session Detail. Treat missing/invalid stored values as enabled.
- Compact card: a small first line with request number, local time to seconds, and lineage-state icon (for example `#232 13:20:11 [icon]`); a second line with `↓ input tokens` and `↑ output tokens`. Use a short, stable width so many cards fit on a desktop screen. Preserve clear focus and selected states.
- Compact mode has **no explanatory hover tooltips or tooltip labels**. The icon can still have an accessible name for assistive technology. Detailed mode retains the existing lineage-icon hover explanations and the fuller model/status/latency/parent information.
- Detailed mode also saves height: remove the `Scroll right for older requests` helper beneath rows. Put conversation evidence/explanation beside the `Conversation X` heading instead of on lines below it, with responsive wrapping on narrow screens. Keep important uncertainty and auxiliary notices visible without forcing a tall header.
- The row's trailing `More (N total)` control remains. On Dashboard it links to the corresponding Session Detail layout; on Session Detail it loads older cards for the sequence or the relevant conversation. `N` means represented requests for that row, not the number of repeated cards across all conversation rows.

### Shared top section and selection

- On Dashboard, put the activity chart and context info panel **above** the request sequence/conversation rows. Add the same top section to Session Detail's Conversations view. Both activity charts show the **last 10 requests**, not the full session timeline.
- The context panel reflects the **selected request**, defaulting to the newest request in the session. Selecting a card highlights its border and updates the panel. Clicking/tapping the already-selected card again opens Request Detail. Provide an explicit `Open request` affordance in or near the context panel so navigation does not depend on a double action or hover; support keyboard selection and opening.
- Selection survives ordinary polling and rerenders. When a genuinely newer request arrives in the active session, automatically select/follow that newest request. Do not pin an older selection or require a `Follow latest` control. If an existing selection disappears after a revision change, fall back to newest and announce the change discreetly.
- Use the same selected request across Sequence and Conversations layouts when it is represented in the visible cards. If switching layouts makes it temporarily invisible due to paging/group limits, keep the context panel and offer a way to locate/open the request, or explicitly reset to newest with a visible notice; do not silently show context for an unhighlighted different card.
- Design the context panel as a flexible area with room for future actions such as compare and move-to-session. Avoid a narrow, fixed information-only sidebar or an API shape that assumes only text metrics.
- Parent comparison must use the accepted direct parent from backend lineage. Do not compare a selected card with its chronological neighbor. For no accepted direct parent, show an honest unavailable/no-direct-parent state.

## Backend and API work

1. Create a shared Python projection for lineage-aware request cards. Reuse the existing cached session graph and `_flow_item`/conversation card formatting as appropriate. It should return the request's session sequence, timestamp, token totals, status, model/agent/provider where needed, lineage icon/state, and accepted parent metadata. The frontend formats and lays this out; it does not infer parentage, grouping, token aggregates, or context changes.
2. Supply a **session-wide sequence preview** in the dashboard live response, rather than the current five-card `_LIVE_FLOW_LIMIT` fallback. Aim for roughly 15 initially visible cards on a wide display, with a total count and a continuation cursor/link. Keep it chronological by stable `session_seq DESC` (with a deterministic fallback for legacy/missing sequence values). Do not assemble this by concatenating conversation previews: that would duplicate shared history and omit auxiliary requests.
3. Add a revision-bound paged Session Detail sequence API (or an equivalent shared endpoint) with a bounded page size, `next_cursor`, and exact session request total. Use the same lineage revision as conversation pages. A stale revision returns `409`, causing the UI to refresh its preview and discard stale pages. Reuse the backend projection for both dashboard and Session Detail rather than maintaining two definitions of a card.
4. Expose backend-computed activity points for the last 10 session requests to Session Detail (either in the sequence response or a shared summary endpoint). Do not aggregate raw blocks or derive token/category totals in TypeScript.
5. Add a request-specific context summary endpoint or query parameter keyed by session and selected request ID, with revision validation. Generalize the current group-latest `_context_change` computation to work for any selected request and its **accepted direct parent only**. Return explicit comparison fidelity/unavailability and the selected request identity; avoid N+1 context calculations across the preview.
6. Keep existing `/sessions/{id}/conversations` and `/conversations/requests` behavior for grouped rows. The new sequence and selected-context projections should share the same graph/revision semantics and avoid rebuilding lineage separately for each card.

Lineage and conversation membership remain **derived on demand**, with the current in-process graph cache; they are not stored beside `Request` as durable analysis. The database already stores the capture evidence (provider IDs, stream hints, blocks, etc.). This UI project should not add persisted lineage columns or a migration. If future actions need durable user overrides, plan those as a separate, explicit source of truth rather than silently persisting heuristic output.

## Frontend work

1. Refactor `RequestFlow` into a reusable selectable row/card used by Dashboard and Session Detail. The card's primary activation selects; a second activation on the same card navigates. Use button semantics (`aria-pressed` or equivalent) and an explicit link to Request Detail; do not keep an `<a>` whose first click is intercepted unpredictably. Make touch and keyboard behavior testable.
2. Build a shared `SessionRequestOverview`-style composition: activity chart and selected context panel first, then the layout/compact controls and either sequence row or grouped rows. Dashboard and Session Detail can supply different paging/navigation callbacks without forking card behavior.
3. Refactor `ContextChangePanel` from “Latest request” to selected-request labeling and a flexible action area. Ensure context loading/empty/error states do not move the controls or imply a parent comparison that the backend did not establish.
4. In `SessionConversationSequences`, preserve existing group paging, segment index, gap labels, and focused segment window. Remove per-group `Show context` buttons: card selection drives the shared context panel. Compact the header as specified and remove the scroll-right helper.
5. Store the compact preference in one shared hook/key. Keep layout state in the URL for Session Detail and appropriate local state/navigation for Dashboard. Do not let layout or compact changes alter the selected request unintentionally.
6. Keep horizontal scrolling and a trailing More action usable at large and small widths. The chart/context region should stack responsively; give the context panel enough width for future actions. Avoid nesting the new top section inside a cramped row panel.

## Edge cases and verification

- Zero requests: show the existing empty state, with no misleading context or activity. One group: Sequence by default; no redundant `1 conversations` switch. Auxiliary-only session: its requests appear once in Sequence and can appear as an Auxiliary requests row in Conversations when the switch is available/linked explicitly.
- Multiple conversations and shared ancestry: sequence is deduplicated and capture-ordered; grouped rows retain existing shared-history semantics and evidence. Confirm count labels mean the right thing in each layout.
- Missing or ambiguous parent: icon and detailed explanation retain their existing meaning; the context panel does not fabricate an adjacent-request comparison. Compact mode has no hover explanation.
- Live capture: a new request resets selection to newest exactly once. Polls with unchanged newest ID, background query refreshes, page loads, and compact/layout toggles do not reset selection. Handle stale cursors/revisions with the established `409` refresh pattern.
- URL compatibility: old conversation deep links and `mode=fragments` still work; switching layouts preserves session and unrelated query parameters.
- Test Python card/sequence projection, revision and pagination, exact totals, activity last-10 window, selected context with accepted/no/ambiguous parent, auxiliary-only and shared-history cases. Run `pytest` for backend changes.
- Test frontend first/second card activation, explicit Open action, keyboard/touch-friendly semantics, selected border, live auto-follow, cross-layout selection, compact preference persistence across both screens, no compact tooltips, detailed tooltip retention, header layout, More behavior, and old deep links. Run `cd ui && npm test` and `cd ui && npm run build` (or `make ui`) after UI changes.

## Implementation order

1. Add and test the backend sequence/card/activity/selected-context projections with shared graph revision behavior.
2. Refactor shared card, selection state, context panel, and persisted compact setting.
3. Recompose Dashboard, then Session Detail, using the shared pieces; preserve grouped paging and fragments.
4. Complete responsive/accessibility polish, update any user-facing request-tracking documentation affected by the new presentation, and run the relevant backend/frontend suites and UI build.

No merge, migration, or lineage-algorithm change is part of this plan.
