# Merge the live dashboard with conversation-aware request sequences

## Status and ownership

The user merged `main` into `merge_attempt_dashbort_and_branches` at `61806c9`. The combined
baseline passed before implementation. The conversation-aware dashboard and shared backend path
projection are now implemented; backend and UI suites pass. Manual visual QA with live captured
traffic remains a follow-up. The implementation in `main` was the source of truth for the live
dashboard; `plans/archive/new-dashboard.md` records its original single-sequence design.

Goal: preserve the live active-session dashboard while showing a separate recent request
sequence for each conversation in that session. Context changes must compare a request with its
resolved conversation parent, never merely with the preceding session request number.

This plan changes neither the meaning of `Session` nor the meaning of `Request`. A session is a
named recording window. A request is one provider invocation. A conversation is a derived path
through primary `context_continuation` edges. Independent roots form separate conversations;
forked paths are separate conversations that may share earlier requests. Conversation membership
is computed at read time and can change as new evidence arrives. `session_seq` remains a stable
session-local recording label, not a conversation position.

For example, a session containing `#1 -> #2`, `#1 -> #3`, and unrelated `#4` has three
conversation paths: `#1 -> #2`, `#1 -> #3`, and `#4`. Request `#1` belongs to both forked paths.
The main-branch dashboard currently displays one strip such as `#4, #3, #2, #1` and compares
`#4` with `#3`. Neither adjacency nor that comparison establishes a context relationship.

## 1. User-owned merge boundary

1. The user merges the current `main` tip into `merge_attempt_dashbort_and_branches` (or the
   eventual integration branch) and reports the resulting commit or any conflicts. Codex does
   not invoke or commit the merge. If conflicts remain, inspect them together before implementation.
2. After the merge, inspect the combined diff and re-run the baseline tests before changing
   dashboard behavior. Do not interpret a clean textual merge as a correct product merge.
3. Preserve both sets of functionality when resolving overlap:

   | Area | Keep from `main` | Keep from the conversation branch |
   | --- | --- | --- |
   | `contextspy/db/crud.py` | Cached-token statistics and `get_dashboard_live()` | Atomic `session_seq`, bulk lineage snapshots, parent-relative context diffs |
   | `contextspy/db/models.py`, `database.py`, `migrations.py` | Cached statistics' existing source fields | `started_at`, `next_request_seq`, schema v5 repair and unique session-sequence index |
   | `ui/src/api/client.ts`, `hooks.ts` | Dashboard types, query, and create/rename invalidation | Lineage/context-diff types, hooks, and endpoints |
   | `ui/src/pages/Dashboard.tsx` | `LiveSessionSection` and original overview sections | Session terminology |
   | `ui/src/pages/SessionDetail.tsx` | `CacheSplit` and PDF cache data | Summary/Conversations switch and graph |
   | `ui/src/components/SessionControls.tsx` | Shared `StartSessionDialog` extraction | Session terminology and existing Sessions-page behavior |
   | Plans/docs | Main's archive moves and dashboard description | Request-lineage plan and updated Session/Conversation terminology |

The three-way preview at planning time showed no conflict markers, but these shared files need
manual inspection. Main adds `AGENTS.md`; its rule that analysis and aggregation belong in Python
applies to the combined tree. The current conversation UI computes some path membership and counts
in React; move that derivation to the backend during integration.

## 2. Merge-ready baseline checks

After the user merge, verify the combined tree before implementing new behavior:

- `/api/stats/dashboard-live` returns the active session, global request flow, activity, and
  structural context change from `main`.
- `/api/sessions/{id}/lineage` and `/api/requests/{id}/context-diff` still work, including exact
  external parents, conservative inferred edges, forks, and ambiguity diagnostics.
- The dashboard has the active-session panel and the older global **Recent requests** table.
  The global table is an audit list and may remain flat and chronological.
- Session Detail has both cached-token statistics and its **Conversations** view.
- Schema version remains 5; the v5 migration and atomic request numbering are intact.
- Run the backend suite and `cd ui && npm run check`. Inspect failing tests before changing their
  expectations. The production UI build under `contextspy/_web/` is ignored by Git.

## 3. Backend conversation projection

The dashboard must consume the same relationship decisions as the Session Conversations view.
Do not add a second heuristic based on request timestamps, adjacency, model, agent, or connection.

1. In `contextspy/analysis/lineage.py`, derive ordered root-to-leaf conversation paths from the
   graph's primary `context_continuation` edges. Every in-session request must appear in at least
   one path. A shared prefix appears in each descendant path without duplicating Request rows.
   Exclude delegation and contribution overlay edges from path identity.
2. Expose a backend path projection usable by both `GET /sessions/{id}/lineage` and
   `get_dashboard_live()`. Include path request IDs in root-to-leaf order, the root and leaf IDs,
   an opaque read-time path key, a display label, and the edge certainty/parent state for each
   step. A path key is a UI selection key, not a persisted conversation ID. Avoid treating
   `lineage_number` or `branch` as durable identity: their numbering may change on refresh.
3. Return per-request conversation memberships in the lineage response, or derive them in the
   same Python projection. Remove the duplicated membership/count calculation from
   `ui/src/components/SessionLineage.tsx` and `ui/src/pages/SessionDetail.tsx`; the UI should only
   render backend-provided paths and labels.
4. Keep the uncertainty model: exact provider links take priority; inferred links carry confidence;
   ambiguous, unresolved-exact, and unavailable parents retain explicit states. Do not attach an
   ambiguous node to a guessed predecessor to make the dashboard look continuous. Treat its
   displayed path as provisional and label the reason.
5. Include external exact parents as references when available. The active-session request strips
   contain only requests belonging to the active session. A path entering from another session
   may show a compact “continued from another session” link; it must not inflate active-session
   request or token totals.

Prefer a single backend implementation of path enumeration and membership. `build_lineage_graph()`
already provides nodes, edges, roots, and branch metadata; extend or project that result rather
than changing the exact/inferred parent rules.

## 4. Extend the live dashboard contract

Keep `GET /api/stats/dashboard-live` as the Overview page's single live data source. Add a
`conversations` collection and a total path count. The endpoint may keep the existing flat
`request_flow` for compatibility, but the new UI must render `conversations`. Keep `activity` as
session-wide input/output activity and label it accordingly. The top-level `context_change`
should be removed from the UI or made an explicitly documented compatibility alias for the most
recent conversation; it must not continue to imply that global session adjacency is lineage.
Use `parent_request_id` and `parent_session_seq` in each new conversation comparison; reserve
the existing `previous_*` fields only for a temporary compatibility alias if needed.

Suggested shape (field names can be refined with tests before implementation):

```json
{
  "active_session": { "id": "s1", "name": "Investigation", "request_count": 4 },
  "conversation_count": 3,
  "conversations": [
    {
      "key": "opaque-derived-path-key",
      "label": "Conversation 1.2",
      "root_request_id": "r1",
      "latest_request_id": "r3",
      "latest_parent_state": "exact",
      "request_flow": [
        { "id": "r3", "session_seq": 3, "parent_request_id": "r1", "certainty": "exact" },
        { "id": "r1", "session_seq": 1, "parent_request_id": null, "certainty": null }
      ],
      "has_older_requests": false,
      "context_change": {
        "request_id": "r3",
        "parent_request_id": "r1",
        "parent_session_seq": 1,
        "parent_state": "exact",
        "token_delta": 1200,
        "comparison_fidelity": "complete",
        "block_changes": []
      }
    }
  ],
  "has_more_conversations": false,
  "activity": []
}
```

The example omits existing session and request fields for brevity. Use the existing
`DashboardRequestFlowItem` card fields (model, times, duration, status, input/output tokens).
Expose observed `started_at` where useful, while retaining `timestamp` as completion time.

### Ordering and limits

- Sort conversation rows by their latest in-session request, newest first by `session_seq` with
  deterministic timestamp/ID fallback. Keep the existing newest-first card order within each row.
- Show the four most recently active conversations on Overview, up to five recent requests per
  conversation. Return `conversation_count` and `has_more_conversations`; link to the full Session
  Conversations view when additional paths exist. These are presentation limits, not analysis
  limits. When there are one to four conversations, show all of them.
- Enumerate the graph before slicing paths or cards. The existing global ten-request query cannot
  determine parentage or recover an older ancestor when conversations interleave.
- A shared request may appear in two strips with the same request UUID and session number. Mark
  it as shared history in the row or card accessible text; do not add its tokens twice to the
  active-session totals.

### Context comparison

- Reuse `main`'s `_context_change()` structural block-count and signed token-delta semantics,
  but pass the latest request's resolved primary parent. Batch the block-count query across all
  displayed parent/child pairs instead of querying once per conversation. Optional parent-relative
  persisted/promoted/added/removed detail can link to the existing context-diff endpoint.
- If the parent is exact or inferred and available, compare with that parent, even when it is not
  the preceding session number. Show “inferred” and confidence beside an inferred comparison.
- If an exact parent is in another session, a comparison may use it, with an explicit external
  parent label; its tokens remain outside active-session totals. If its data is unavailable,
  return a missing-parent state and no delta.
- For a genuine root, use “First request in this conversation.” For ambiguous, unresolved, or
  unavailable parent states, use a specific “Parent uncertain/missing” message and a null delta.
  Do not substitute the previous session request.
- Preserve the distinction between net block-type count changes and semantic content diffs. A
  zero structural delta does not prove unchanged content.

## 5. Dashboard UI

1. Keep `ActiveSessionPanel` and its all-session totals. Replace the one-strip `RequestFlow` with
   a conversation section containing one row per returned path. Reuse request-card markup and
   links; show the conversation label, exact/inferred/uncertain status, latest activity, and a
   shared-history indication at forks. For one path, the section still reads naturally.
2. Keep the full global **Recent requests** table at the bottom of Overview. Its chronological
   role is distinct from the grouped live conversation sequences.
3. Keep `RequestActivityChart` as the session-wide ten-request bar chart and label it **Session
   activity**. It is an overall traffic view; conversation grouping is conveyed by the rows.
4. Drive `ContextChangePanel` from a selected conversation row. Default to the most recently
   active path, allow selecting another visible path, and keep the selection by the opaque path
   key across live refreshes when possible. If a path disappears or its key changes as lineage is
   recomputed, select the latest path and announce the change politely. Show the comparison
   request and certainty clearly.
5. Keep session start/end, loading, error, and zero-request behavior from `main`. A session with
   requests but no reliable parent edges still shows separate, uncertainty-labelled paths.
   Failure to load conversation analysis must not blank the other Overview sections.
6. Use a responsive vertical stack of conversation rows; each row's cards may scroll horizontally
   without document-level overflow. Preserve keyboard order and accessible path/card labels.
   The “N more conversations” link opens `/sessions/{id}?view=lineage`.

Frontend files expected after the merge: `ui/src/components/dashboard/LiveSessionSection.tsx`,
`RequestFlow.tsx` (or a wrapper `ConversationFlows.tsx`), `ContextChangePanel.tsx`,
`RequestActivityChart.tsx`, `ui/src/api/client.ts`, `ui/src/api/hooks.ts`, and their tests.

## 6. Query lifecycle and performance

- Continue using `useDashboardLive()` and its `['stats', 'dashboard-live']` key. `new_request`,
  `session_started`, and `session_ended` invalidate the `['stats']` prefix; create/rename/end
  mutations must keep the merged invalidations from `main`. Do not join separate dashboard and
  lineage queries in React: they can describe different snapshots during live updates.
- Reuse one lineage analysis result per live response. The branch's
  `get_session_lineage_snapshots()` loads all requests and block metadata for a session; calling
  it on every five-second poll could be expensive for a long session. Measure a representative
  500-request and 2,000-request session. If needed, cache the derived graph/paths per session and
  a request-set revision (including analysis version), and recompute when requests change.
- Keep aggregate totals and the ten-point activity query in SQL as in `main`. Hydrate only the
  displayed path cards and comparison parents after choosing paths. Batch request and block
  lookups; avoid one query per path or per card.
- No new table or data migration is required for conversation paths. Retain v5 migration and
  uniqueness checks from the conversation branch.

## 7. Verification

Backend tests should cover:

1. One session with one path preserves the existing live experience.
2. Interleaved independent paths become separate rows; neither compares with the other's latest
   request.
3. `#1 -> #2` and `#1 -> #3` produce two paths with a shared `#1`, and `#3` compares to `#1`.
4. A parent older than the ten-request activity window is still found before UI limits apply.
5. Exact predecessor IDs win over a high-scoring inferred alternative; inferred paths expose
   confidence; ambiguous and unresolved parents never receive fabricated deltas.
6. An external exact parent is labelled, excluded from session totals, and compared only when
   its data is available.
7. Counts and labels remain deterministic with null `session_seq`, overlapping start times,
   purged block content, partial/opaque fidelity, and live additions.
8. The dashboard endpoint performs bounded batched lookups without per-path query growth.

Frontend tests should cover one/multiple/forked conversation rows, newest-first order within
each row, shared request links, the selected path's context panel, uncertainty and external-parent
copy, hidden-path count/link, empty/error states, keyboard access, and narrow-width overflow.
Update `main`'s dashboard tests that assert the old global predecessor comparison;
retain its tests for session totals, card contents, activity axes, and live invalidation.

After implementation run `pytest` and `cd ui && npm run check`; build the production UI and
manually inspect a session with interleaved independent and forked requests. Verify that a new
request refreshes the correct path without moving another path's context delta to it.

## Done when

- Main's live dashboard and the branch's Session Conversations view both work in one build.
- A session with multiple conversations shows multiple recent sequences; forks show shared history.
- Every displayed context comparison uses the resolved parent with visible certainty or a clear
  no-comparison state. No dashboard comparison relies on adjacent `session_seq` values.
- Session-wide totals and activity remain correctly scoped to the active session.
- Backend owns conversation membership and comparison decisions; UI renders the returned result.
- Combined backend tests, frontend checks, production build, and manual multi-conversation QA pass.
