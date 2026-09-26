# Session Detail: conversation sequences and lineage diagnostics

## Status and scope

**Plan only; not implemented.** This follows
[`dashboard-conversations-merge-plan.md`](dashboard-conversations-merge-plan.md). Do not merge,
rewrite branches, or change the lineage classification rules as part of a presentation-only
implementation without a separate, tested reason. The working tree was clean when this plan was
written.

The current `/sessions/{id}?view=lineage` page correctly uses the backend's
`conversation_count`, but its only main visual is the raw `SessionLineage` graph. In the observed
`codex 0925` session it showed **one supported conversation and 28 diagnostic lineage paths**,
yet drew those paths as many long lanes. Request #178 also received a bare “Fork” badge for a
structural exact/inferred branch that did **not** establish a second conversation. The count is
right; the visual hierarchy is not yet the conversation view promised by the earlier plan.

Goal: make Session Detail answer “what conversations were captured?” first, using exactly the
dashboard's conservative stream semantics, while retaining detailed request-parent evidence in
an explicitly diagnostic second mode. A gap, path, model change, or generic `agent` label must
never become a separate conversation merely because of how the screen is laid out.

## Product decision: two modes under Conversations

Keep the existing top-level **Summary | Conversations** control. Inside Conversations add:

| Mode | Default? | Purpose | Source of truth |
| --- | --- | --- | --- |
| **Conversation sequences** | Yes | All backend-confirmed groups, their requests and uncertain segments, using the same grouping/evidence vocabulary as Overview | Shared Python conversation projection |
| **Lineage diagnostics** | No | Exact/inferred parent edges, raw root-to-leaf paths, ambiguous candidates, unresolved/external parents, and block deltas | Existing lineage graph |

Use `?view=lineage` (including existing Overview links) for the default conversation mode.
Use `?view=lineage&mode=fragments` for diagnostics. Do not rename `view=lineage` yet: existing
links and saved URLs must continue to work. Invalid `mode` values fall back to conversations.
Changing modes preserves the session ID, keyboard focus, browser back/forward behavior, and any
unrelated query parameters. A future route cleanup can rename the legacy `view` parameter
separately.

“Conversation” means a supported display stream, not a root-to-leaf path. The backend's
`lineage_fragment_count` currently counts diagnostic paths, some of which can share ancestors;
the UI should label this **diagnostic paths**, not “fragmented conversations” or “independent
chats.” A **lineage gap** is a missing, ambiguous, unavailable, or not-established parent within
a display group. A graph branch may be diagnostic without being a confirmed conversation fork.

## Conversation sequences: desired screen

1. The first line states the backend counts in plain language: “1 conversation sequence · 28
   diagnostic paths” in the observed case, with a short explanation that paths are evidence, not
   extra conversations. Do not make “28” the visually dominant count. Empty sessions show the
   current empty state.
2. Show the primary/default sequence, then every confirmed additional group, in a responsive
   vertical stack. Keep backend `key`, `label`, and `evidence` semantics identical to Overview:
   `default`, `fork` with its proven parent, or `parallel_chains`. For zero confirmed parallel
   streams use **Session request sequence** and say “Separate streams not confirmed”; do not call
   its disconnected segments a proven single chain. Do not create a row per path.
3. Each group header shows the number of in-session requests represented, latest activity,
   backend gap/segment count, evidence, and a selected-group context-size panel. Group request
   counts may include proven shared fork ancestry in more than one group; the session total counts
   each stored request once. Mark shared cards explicitly. Never add group counts to obtain
   session totals.
4. Render ordered **segments of accepted continuation edges** within each group. Show the break
   before a segment using the backend reason: ambiguous parent, missing exact predecessor,
   unavailable context, unestablished root, external predecessor, or an internal structural
   branch. A structural branch inside the one default group must say “graph branch; separate
   conversation not confirmed,” not simply “Fork.” Do not draw an edge across a gap or imply that
   adjacent `session_seq` values are parent/child. Show exact/inferred certainty and confidence
   where there is a resolved parent. Show `membership_state: unassigned` as an uncertainty cue,
   not as a new group. An exact external parent can be linked/labeled but is never an in-session
   card or part of session totals.
5. Start with up to five newest requests per shown group, as on Overview. “Show earlier requests”
   loads additional pages within that group and retains segment breaks. The full Session Detail
   can reveal every request and every confirmed group; it must not inherit Overview's four-group
   or five-card *total* limits. Provide “Jump to next gap”/a compact segment index for a long
   single sequence so the user can explore older discontinuities without scrolling hundreds of
   cards. Request cards link to Request Detail.
6. The context panel compares the group's latest request only with its resolved primary lineage
   parent. Reuse Overview's labels for exact/inferred confidence, external parent, partial/opaque
   fidelity, and no-comparison states. Preserve the distinction between structural block-count
   changes and semantic content changes. Selecting another group changes the panel; selection
   never creates a parent edge. A stale selected key falls back to the primary group with a
   polite status message, as on Overview.
7. Share presentation primitives with the dashboard (card, evidence badge, gap marker, context
   panel), but do not make Session Detail consume `/api/stats/dashboard-live`: that endpoint is
   active-session-only and intentionally truncates groups and cards. Keep a single vocabulary,
   ordering rule, and backend-derived facts across both screens.

## Lineage diagnostics: preserve evidence without the giant default canvas

1. Move the current `SessionLineage` graph behind **Lineage diagnostics**. Its heading, SVG
   accessible name, legend, and table should say “lineage graph/paths,” not “conversation graph.”
   Keep exact/inferred edges, confidence, ambiguous candidates, unresolved/external parents,
   block-delta inspection, request links, and the table fallback.
2. Do not initially lay out every path as one enormous 2-D canvas. Show a searchable, newest-first
   index of backend diagnostic paths with root/leaf request number, request count, last activity,
   and start-parent state. Selecting one path (or connected component if a branch shares ancestry)
   shows only that subgraph and its relevant table rows. Provide explicit navigation to the next
   path and an optional full-graph control for small sessions only. For a long selected chain,
   show a bounded recent window with “Earlier nodes”; do not create a 200,000px-wide canvas.
   Path selection is a display choice; the backend remains responsible for deciding path
   membership and parent states.
3. Separate **structural graph branch** from **confirmed conversation fork**. Keep the raw
   `is_fork` fact, but obtain a backend `fork_status`/`conversation_fork_status` or equivalent
   before displaying a conversational claim. A branch like #178 in the observed session should
   be labeled “Graph branch · conversation split unconfirmed.” Proven fork parents can say
   “Confirmed conversation fork.” Never derive this status from React lane counts.
4. Preserve selected edge/node details when they remain in the selected subgraph. If a live
   request changes the path list or the selected leaf, fall back to the nearest surviving anchor
   (root or selected request) and announce the change. Do not silently jump to another path.

## Backend/API work

The existing `build_lineage_graph()` already returns `conversation_count`, group membership,
raw `lineage_paths`, parent states, and edges. The dashboard then constructs preview segments in
`db/crud.py`; Session Detail currently has only group `request_ids`, not an ordered, paged
conversation display contract. Refactor that presentation projection into one Python service
used by both endpoints. Do not reproduce grouping, gap classification, fork classification,
token differences, or aggregates in TypeScript.

1. Define a shared conversation-view DTO/projector from one lineage result. It owns deterministic
   group order and keys; segment IDs anchored to a root or branch-start request (not branch
   ordinals); segment start/break reason; per-card parent ID, certainty/confidence, membership,
   shared-history state; `fork_status`; and per-group counts/context comparison. Keep raw graph
   paths as separate diagnostics. Preserve existing response fields for dashboard compatibility.
2. Add `GET /api/sessions/{id}/conversations` for **any** session, active or ended. Return the
   session ID/revision, `conversation_count`, `confirmed_parallel_streams`,
   `lineage_fragment_count`, primary key, group summaries, recent preview segments, and group
   pagination metadata. Example group fields: `key`, `label`, `evidence`,
   `fork_parent_request_id`, `request_count`, `unassigned_request_count`,
   `unlinked_segment_count`, `latest_request_id`, `context_change`, `recent_segments`, and
   `next_request_cursor`. Return primary plus the most recently active three secondaries on the
   first page, with a cursor to reveal the rest. Counts/classification cover all requests before
   limits are applied.
3. Add a paged group-requests route (or equivalent query on the same endpoint) taking the opaque
   backend group key, revision, and keyset cursor; default about 50 cards. Return ordered segment
   slices, a `continues_earlier` marker when a page cuts through a linked segment, and the next
   cursor. Validate that the group belongs to the requested session. If a live append changes
   classification or invalidates a cursor, return a documented refresh condition rather than
   silently mixing revisions. `session_seq` alone is not a cursor because it may be null; use
   `(session_seq, timestamp, id)` (or an opaque encoding of the same deterministic order).
4. Keep `/api/sessions/{id}/lineage` for diagnostic consumers. Extend it only with backend
   path summaries/fork status needed by the diagnostics UI, or add a small diagnostic summary
   route if the full graph proves too large. Do not remove full graph data or change exact/inferred
   parent rules in this feature. External parents remain graph nodes but not group cards.
5. Give each response one SQLite read snapshot across graph, summaries, cards, and comparison
   parents, as the live dashboard already does. Generalize/reuse its bounded graph cache for
   non-active sessions, keyed by session, analysis version, and request-set revision; invalidate
   on new/deleted requests and relevant migration/reanalysis changes. Batch card and block-count
   hydration, and avoid per-group/per-card queries. No database schema migration is expected.

## Frontend structure and query lifecycle

- `SessionDetail.tsx`: keep Summary/Conversations; add the nested URL-backed mode control and
  route each mode to its own component. Default `view=lineage` to the conversation sequences,
  including old bookmarks and the dashboard's “View all conversations” link.
- New `SessionConversationSequences` (name flexible): render the session projection, group
  selection, preview/page loading, segment index, and selected context panel. Extract reusable
  presentational pieces from `ConversationFlows`, `RequestFlow`, and `ContextChangePanel` instead
  of copying data logic. Different screens may use different density, not different semantics.
- `SessionLineage`: narrow it to diagnostics and selected-path rendering. Its `layoutNodes()` may
  calculate coordinates from backend-provided path IDs; it must not infer conversations, gaps,
  token totals, or fork confirmation. Keep the existing edge-detail interactions and table.
- `api/client.ts`, `hooks.ts`, `useWebSocket.ts`: type the new contract and use a session-scoped
  query key. Invalidate/refetch it on `new_request`, session end/rename/delete, and relevant
  session changes. Fetch the full diagnostic graph only when the diagnostics mode is open; do not
  join conversation and graph responses from different live revisions into one claim. Preserve
  stable selected group keys across refreshes.
- At narrow widths, stack group sections and allow only bounded card strips to scroll
  horizontally; no document-level overflow. Use real buttons for modes, groups, segment index,
  and pagination; expose counts, evidence, gaps, shared history, and current selection in text
  and accessible labels. Restore focus sensibly after loading more or switching modes.

## Verification and acceptance

Backend tests: one accepted chain; a Codex-like session with many roots/ambiguous starts and no
confirmed split; a confirmed exact sustained fork with shared ancestry; an unconfirmed
exact/inferred structural branch such as #178; sustained disjoint interleaved chains; external
exact parents; ambiguous/unavailable parents; null sequence numbers; group and card pagination;
cursor invalidation on live append; totals counted once; read-snapshot concurrency; bounded query
count and 500/2,000-request performance. Dashboard and Session Detail must return the same
group keys, evidence, membership, segment reasons, and latest-parent comparison for the same
request-set revision.

Frontend tests: legacy `?view=lineage` opens conversation mode; mode URL/back-forward behavior;
one group with many gaps; multiple confirmed groups; shared prefix; selected-group comparison;
no invented parent across a gap; group/card loading; stale-key fallback; diagnostic path search
and selection; unconfirmed graph branch wording; empty/error/loading cases; keyboard access and
narrow viewport. Build the production UI after touching `ui/src/`.

Manual QA: check the observed `codex 0925` session shows **one** conversation sequence while
the diagnostics mode reports its many raw paths; inspect the #178 branch wording. Check a
synthetic confirmed fork and two independent parallel chains. Verify neither mode creates a
page-wide horizontal scrollbar at desktop or phone width and that active capture updates the
right group without mixing snapshots.

Done when the default Conversations screen visibly groups by supported streams, diagnostics are
clearly optional, and every count/edge/break has the same meaning as on Overview. Do not mark
this plan implemented solely because the tab badge uses the backend `conversation_count`.
