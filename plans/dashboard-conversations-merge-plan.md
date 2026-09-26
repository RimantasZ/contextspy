# Conversation-aware dashboard without false conversation splits

## Status and ownership

**Revised plan; not implemented on this branch.** The earlier implementation and its status note
belonged to the discarded `merge_attempt_dashbort_and_branches` branch. The clean integration
branch is `merge_sequence_refactor_with_new_dash_2` at `621c206`: it contains
`refactor-request-sequence-tracking` and the current `main` tip (`9be0610`). Before this revision,
the baseline passed: 210 backend tests, 82 frontend tests, UI lint/typecheck, and production build.
The working tree was clean. `main` remains the source of truth for the live dashboard;
`plans/archive/new-dashboard.md` records its original single-sequence design.

Goal: preserve the live active-session dashboard while showing separate recent sequences **only
when there is positive evidence of distinct parallel request streams** (independent agents/tasks
or a fork). Context changes must compare a request with its resolved lineage parent, never merely
with the preceding session request number.

This plan changes neither the meaning of `Session` nor the meaning of `Request`. A session is a
named recording window; a request is one provider invocation. A **lineage path** is a root-to-leaf
chain of accepted `context_continuation` edges. A **conversation** is a supported request stream;
it may contain multiple disconnected lineage fragments. When no separate stream is proven, the
UI uses one **session activity** fallback group without asserting that its fragments are one
proven chain. An unresolved parent creates a *gap*, not a new conversation. `session_seq` remains a stable
session-local recording label, not a conversation position or proof of parentage. Conversation
grouping is computed in Python at read time, with explicit evidence and uncertainty.

For example, `#1 -> #2` and `#1 -> #3` are a proven fork and may be shown as two conversations
sharing `#1`. An unlinked `#4` is **not automatically a third conversation**; it is shown as an
unlinked segment until independent-stream evidence exists. A single ongoing Codex chat can have
many such segments because provider predecessor IDs are absent, context is opaque, or several
parent candidates tie. In the observed `codex 0925` session, the old path-count rule produced 15
"conversations" despite no detected forks. This must be a regression case, not the desired UI.

## 1. User-owned merge boundary

1. The user has already merged `main` into `merge_sequence_refactor_with_new_dash_2`. Codex must
   not merge or rewrite branches. Recheck branch and worktree status before implementation.
2. The combined baseline has passed. Do not interpret a clean textual merge as a correct product
   integration. Preserve these areas during implementation:

   | Area | Keep from `main` | Keep from the conversation branch |
   | --- | --- | --- |
   | `contextspy/db/crud.py` | Cached-token statistics and `get_dashboard_live()` | Atomic `session_seq`, bulk lineage snapshots, parent-relative context diffs |
   | `contextspy/db/models.py`, `database.py`, `migrations.py` | Cached statistics' existing source fields | `started_at`, `next_request_seq`, schema v5 repair and unique session-sequence index |
   | `ui/src/api/client.ts`, `hooks.ts` | Dashboard types, query, and create/rename invalidation | Lineage/context-diff types, hooks, and endpoints |
   | `ui/src/pages/Dashboard.tsx` | `LiveSessionSection` and original overview sections | Session terminology |
   | `ui/src/pages/SessionDetail.tsx` | `CacheSplit` and PDF cache data | Summary/Conversations switch and graph |
   | `ui/src/components/SessionControls.tsx` | Shared `StartSessionDialog` extraction | Session terminology and existing Sessions-page behavior |
   | Plans/docs | Main's archive moves and dashboard description | Request-lineage plan and updated Session/Conversation terminology |

The current conversation UI computes path membership and counts in React; move classification and
counts to Python per `AGENTS.md`. Keep raw graph paths available as diagnostics, but do not use
their count as the conversation count.

## 2. Merge-ready baseline checks

Confirmed on `621c206`; recheck if the branch moves before implementation:

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

## 3. Backend: lineage evidence versus conversation grouping

Use one backend analysis result for Session Detail and Overview. Keep the existing exact/inferred
parent rules and diagnostic graph; add a **separate, conservative grouping layer**. Do not infer a
conversation boundary from request-number adjacency, a missing predecessor, model changes, the
generic `agent` label (`codex`), or elapsed time alone.

1. In `contextspy/analysis/lineage.py`, enumerate primary root-to-leaf **lineage paths** for
   diagnostics. Preserve exact edges, inferred confidence, ambiguous candidates, unresolved
   predecessor IDs, external parents, and fork topology. Overlay delegation/contribution edges
   do not establish context continuation. A path ending at a gap is a fragment, not necessarily
   a conversation.
2. Build conversation/display groups from *positive* evidence, in this order:
   - **Confirmed fork:** one request has two or more accepted primary continuation children, and
     their observed invocations overlap or both children lead to sustained, distinct descendant
     chains. A lone retry/replay sibling is not enough. Distinct descendant branches are separate
     streams; shared ancestors belong to both.
   - **Explicit independent stream:** a provider/agent supplies a documented stable task,
     thread, or subagent identifier; different identifiers and overlapping/interleaved activity
     establish distinct streams. Audit the available capture metadata first. `Request.agent`
     alone is only a product name, not such an identifier.
   - **Strong observed parallelism without IDs:** two disjoint chains each contain at least two
     accepted continuation edges (three requests), have observed start times with sustained
     interleaving (at least A–B–A–B in start order), and have no shared ancestor. Exact provider
     predecessor IDs remain authoritative; inferred own-chain parents must satisfy the existing
     inference rule. Cross-chain candidates must remain below the existing `AMBIGUOUS_THRESHOLD`
     after excluding configuration-only matches. Reject this rule if any plausible ambiguous
     cross-parent remains. Estimated/completion-only starts, two isolated roots, or one-off helper calls do
     not qualify by themselves. Validate this conservative predicate against real and synthetic
     captures before enabling it.
3. If none of these rules confirms a second stream, expose **one session activity group** plus
   explicit lineage gaps, regardless of the number of graph roots/leaves. Do not silently claim
   that all its requests form one proven context chain. If parallel streams are confirmed, choose
   the primary group deterministically (most in-session requests on accepted edges, then latest
   activity and ID); show other confirmed groups separately. Put requests that cannot be assigned
   confidently in the primary/default view with `membership_state: unassigned`, never into a
   guessed secondary stream. Duplicate only proven shared fork ancestry across groups.
4. Return both diagnostic `lineage_fragment_count` and display `conversation_count`. The latter
   is zero for an empty session, otherwise one default group plus confirmed additional streams;
   it must not be `len(leaves)` or the count of `lineage_number:branch`. Return per-request group
   membership and gap reason from Python. Remove the duplicated path membership/count logic from
   `ui/src/components/SessionLineage.tsx` and `ui/src/pages/SessionDetail.tsx`.
5. Each display group contains ordered **segments** of accepted edges, not a fabricated chain.
   Preserve `parent_request_id`, certainty/confidence, and `parent_state` on each request; mark
   breaks between segments. Use an opaque read-time group key anchored to the session and positive
   stream evidence, not a leaf UUID or branch number that changes on every extension.
6. Exact parents in another session may be referenced and used for comparison, but only active-
   session requests appear as cards or contribute to session counts/tokens.

Current `Request` rows have provider response/predecessor IDs, generic `agent`, timing, and block
metadata, but no dedicated Codex thread/subagent ID. Implementation must first inspect whether a
reliable source identifier is actually present in captured metadata. If adding a persisted field
is justified, update `db/models.py`, `db/database.py`, and `db/migrations.py` (including a versioned
backfill only if existing data can be reconstructed reliably). Otherwise leave the schema at v5
and let explicit-ID detection remain unavailable; do not invent identity from opaque payloads.

## 4. Extend the live dashboard contract

Keep `GET /api/stats/dashboard-live` as the Overview page's single live data source. Add grouped
conversation sequences **and** the diagnostic fragment count. Keep the flat `request_flow` only
for compatibility; the new UI renders the grouped response. `activity` remains session-wide.
The top-level `context_change` may remain a documented compatibility alias for the most recently
active group's latest request, but it must use the resolved parent and must not imply that the
previous session number is lineage. Prefer `parent_*` fields over `previous_*` in new data.

Suggested one-chat-with-gaps shape (field names may be refined with tests):

```json
{
  "active_session": { "id": "s1", "name": "Codex work", "request_count": 130 },
  "conversation_count": 1,
  "confirmed_parallel_streams": 0,
  "lineage_fragment_count": 15,
  "conversations": [
    {
      "key": "session:s1:primary",
      "label": "Session request sequence",
      "evidence": "default",
      "latest_request_id": "r130",
      "unlinked_segment_count": 14,
      "latest_parent_state": "exact",
      "recent_segments": [
        {
          "gap_reason": "ambiguous_parent",
          "request_flow": [
            { "id": "r130", "session_seq": 130, "parent_request_id": "r129", "certainty": "exact" }
          ]
        }
      ],
      "has_older_requests": true,
      "context_change": {
        "request_id": "r130",
        "parent_request_id": "r129",
        "parent_session_seq": 129,
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

The example is schematic, not a proposed claim about any particular captured request. It omits
existing card/session fields. Use the existing `DashboardRequestFlowItem` model, time, duration,
status, and token fields. Return backend-computed `membership_state`, segment/gap reason, and
stream evidence. Expose observed `started_at` where useful while retaining `timestamp` as
completion time. A group created by a fork or an independent stream should include its specific
evidence (`fork_parent_request_id`, source stream ID/provenance, or tested parallel-chain signals).

### Ordering and limits

- Return the primary/default group and up to three most recently active confirmed parallel
  groups; sort those secondary rows by latest in-session `session_seq` with timestamp/ID fallback.
  Keep newest-first cards within each group and visibly separate unrelated segments.
- Limit Overview to five recent in-session requests per displayed group, but classify all requests
  before slicing. Return `conversation_count`, `confirmed_parallel_streams`,
  `lineage_fragment_count`, and `has_more_conversations`; link to Session Detail when groups are
  hidden. The ten-request global activity query cannot establish parentage.
- A proven shared fork ancestor may appear in two strips with the same UUID/number. Mark it as
  shared history; session totals still count the Request row once. An uncertain fragment is not
  duplicated into multiple groups.

### Context comparison

- Reuse `main`'s `_context_change()` structural block-count and signed token-delta semantics,
  but pass the latest request's resolved primary lineage parent. Batch block counts across all
  displayed parent/child pairs rather than querying once per group. Optional semantic diff detail
  can link to the existing context-diff endpoint.
- If the parent is exact or inferred and available, compare with that parent, even when it is not
  the preceding session number. Show “inferred” and confidence beside an inferred comparison.
- If an exact parent is in another session, a comparison may use it, with an explicit external
  parent label; its tokens remain outside active-session totals. If its data is unavailable,
  return a missing-parent state and no delta.
- For an independently confirmed root, use “First request in this conversation.” For an ordinary
  graph root with no stream evidence, say “No parent established.” For ambiguous, unresolved, or
  unavailable parent states, use a specific uncertainty message and a null delta. A gap within a
  display group does not authorize comparing adjacent requests.
- Preserve the distinction between net block-type count changes and semantic content diffs. A
  zero structural delta does not prove unchanged content.

## 5. Dashboard UI

1. Keep `ActiveSessionPanel` and its all-session totals. If no second stream is confirmed, show
   **one** recent session request sequence, with visible “lineage gap”/“parent uncertain” markers
   and no claim that all cards are directly connected. Do not show “15 conversations” just
   because the graph has 15 roots/leaves. When parallel streams are confirmed, show one row per
   backend group, with evidence (“fork from #N”, “distinct task/agent stream”, or “parallel
   independent chains”), shared-history marks, and recent activity. Reuse request-card links.
2. Keep the full global **Recent requests** table at the bottom of Overview. Its chronological
   role is distinct from the grouped live conversation sequences.
3. Keep `RequestActivityChart` as the session-wide ten-request bar chart and label it **Session
   activity**. It is an overall traffic view; conversation grouping is conveyed by the rows.
4. Drive `ContextChangePanel` from the selected group. Default to the most recently active group,
   allow selecting another, and retain the backend group key across live refreshes. If a group
   ceases to be supported, select the primary group and announce the change politely. Show the
   actual comparison parent/certainty or the specific no-comparison state. A selected group may
   contain several fragments; selection does not create parent edges between them.
5. Keep session start/end, loading, error, and zero-request behavior from `main`. A session with
   requests but no reliable edges still shows **one** sequence with unknown-continuity markers.
   Failure to load grouping must not blank the other Overview sections; use the flat session
   request flow as a labelled fallback, with parent comparisons unavailable rather than guessed.
6. Session Detail's graph remains a full diagnostic view. Rename raw root-to-leaf lanes/paths as
   **lineage paths/fragments** and show the backend conversation grouping separately. Its
   Conversations tab count must use the backend grouped count, not distinct
   `lineage_number:branch` values. Show the fragment count as a diagnostic, not as a count of
   chats or subagents.
7. Use a responsive vertical stack when multiple confirmed groups exist. Cards/segments may
   scroll horizontally without page overflow. Preserve keyboard order and accessible gap,
   evidence, selection, and card labels. A hidden-groups link opens
   `/sessions/{id}?view=lineage`.

Frontend files expected after the merge: `ui/src/components/dashboard/LiveSessionSection.tsx`,
`RequestFlow.tsx` (or a wrapper `ConversationFlows.tsx`), `ContextChangePanel.tsx`,
`RequestActivityChart.tsx`, `ui/src/api/client.ts`, `ui/src/api/hooks.ts`, and their tests.

## 6. Query lifecycle and performance

- Continue using `useDashboardLive()` and its `['stats', 'dashboard-live']` key. `new_request`,
  `session_started`, and `session_ended` invalidate the `['stats']` prefix; create/rename/end
  mutations must keep the merged invalidations from `main`. Do not join separate dashboard and
  lineage queries in React: they can describe different snapshots during live updates.
- Read session totals, recent activity, lineage snapshots, and displayed parent/card rows from
  one consistent SQLite read snapshot. The previous implementation was observed to return a
  request count of 126 alongside card #127 when capture advanced between queries. Test an append
  during endpoint execution; one response must never mix pre- and post-append state.
- Reuse one lineage analysis result per live response. The branch's
  `get_session_lineage_snapshots()` loads all requests and block metadata for a session; calling
  it on every five-second poll could be expensive for a long session. Measure a representative
  500-request and 2,000-request session. If needed, cache the derived graph/paths per session and
  a request-set revision (including analysis/grouping versions), and recompute when requests
  change. Do not cache a fragment-to-conversation guess as durable identity.
- Keep aggregate totals and the ten-point activity query in SQL as in `main`. Hydrate only the
  displayed group cards and comparison parents after classification. Batch request and block
  lookups; avoid one query per group or per card.
- The grouping projection alone requires no schema change; retain v5 migration and uniqueness
  checks. A new persisted source stream ID is conditional on finding a reliable signal and then
  requires the model/additive/versioned-migration work described in §3.

## 7. Verification

Backend tests should cover:

1. A single accepted chain yields one group. A single ongoing Codex-like chat split into many
   exact chains by missing IDs, five ambiguous starts, opaque contexts, and one-off requests
   still yields **one displayed group**, while exposing every lineage gap and diagnostic path.
2. Two isolated roots, different models, generic `agent` values, sequential chains, overlapping
   singletons, and estimated-only timing never create a second conversation on their own.
3. `#1 -> #2` and `#1 -> #3` with overlapping child invocations or sustained divergent
   descendants produce a confirmed fork: two groups share `#1`, and `#3` compares with `#1`.
   A one-off retry/replay sibling does not create a second conversation. No duplicated totals.
4. Two sustained, disjoint, interleaved chains meeting the documented context-separation rule
   form two independent groups. An ambiguous cross-parent or insufficient observed timing keeps
   them unconfirmed. If an explicit source stream ID is available, test its provenance and scope.
5. A parent older than the ten-request activity window is found before card limits apply. Exact
   predecessor IDs beat inferred alternatives; inferred links expose confidence; ambiguous,
   unresolved, and unavailable parents never receive fabricated edges or deltas.
6. An external exact parent is labelled, excluded from active-session totals/cards, and used for
   comparison only when available. Null `session_seq`, purged content, partial/opaque fidelity,
   overlapping starts, and live additions remain deterministic.
7. Concurrent capture during `get_dashboard_live()` cannot mix totals and cards from different
   snapshots. Queries are batched rather than growing per displayed group; benchmark 500 and
   2,000 requests.

Frontend tests should cover a single row with several marked gaps, true parallel/forked rows,
newest-first cards within segments, shared request links, selected-group context comparison,
uncertainty/external-parent copy, hidden-group link, empty/error states, keyboard access, and
narrow-width overflow. Session Detail must distinguish raw lineage fragments from confirmed
conversation groups. Update `main`'s tests that assume adjacent requests are parent/child;
retain session totals, card contents, activity axes, and live invalidation coverage.

After implementation run `pytest` and `cd ui && npm run check`; build the production UI. Manually
inspect the ongoing single-Codex-session case (many lineage gaps but no proven forks) and a
synthetic session with confirmed independent/forked streams. Verify a new request updates the
correct group without moving an unrelated group's context delta.

## Done when

- Main's live dashboard and the branch's Session Conversations view both work in one build.
- Multiple sequences appear only with confirmed fork or independent-stream evidence. A single
  ongoing chat with many uncertain lineage fragments does not become many conversations.
- Forks show shared history; unresolved requests show gaps and do not gain guessed parents.
- Every displayed context comparison uses the resolved parent with visible certainty or a clear
  no-comparison state. No dashboard comparison relies on adjacent `session_seq` values.
- Session-wide totals and activity remain correctly scoped to the active session.
- One live response is internally consistent even as requests are captured.
- Backend owns conversation grouping, membership, gap and comparison decisions; UI renders the
  returned result.
- Combined backend tests, frontend checks, production build, and manual multi-conversation QA pass.
