# Conversation tracking: production-readiness review and plan

## Status and intent

**Implemented in `dashboard-and-convo-cleanup` on 2026-09-29; release QA remains.** This plan
records the pre-change architectural audit and implementation direction. The user explicitly
authorized the changes in this branch. The baseline findings below describe the earlier code.
The current conversation grouping is acceptable to the user; the default implementation stance
is to preserve its observable decisions while making them easier to explain, maintain, test,
and operate. Keep lineage and conversation analysis in Python, derived from persisted capture
evidence. Do not turn a display group into a durable conversation ID by accident.

Product choices are recorded at the end of this plan. Align terminology within the existing API
without introducing a new API version; keep reasonable compatibility aliases during the UI/API
transition. Any persisted-data change needs the required database migration. Retain on-demand
analysis plus an in-process cache rather than persisting computed lineage or conversation
membership.

Implementation record: analysis value types and conversation projection were extracted; the
graph revision/cache now has a dedicated read service, relevant-evidence invalidation, and a
serialized-size budget; metadata-only snapshot reads, narrower block aggregation, a
revision-polled diagnostics route, shared UI primitives, terminology aliases/labels,
keyboard/touch diagnostic edges, and lazy PDF loading are in place. A synthetic benchmark and
architecture/user docs were added. No schema change or live-data mutation was needed.

Release checks still requiring human judgment: inspect light/dark/mobile/touch layouts on a
real running app; run repeated measurements on a named reference machine to set a meaningful
p95; decide how duplicate provider response IDs should be handled before changing that rare
classification case. The full diagnostics route remains for compatibility; a windowed API is
conditional on real-session transfer/render measurements. The current single-sample synthetic
2,000-request runs are recorded in `docs/conversation-tracking-architecture.md`.

## Current architecture and findings

The pipeline is `normalization.py` and provider adapters → stored `Request`/`BlockRecord`
evidence → `analysis/context_diff.py` and `analysis/lineage.py` → graph cache and presentation
projections in `db/crud.py` → Dashboard/Session Detail components. Direct parent edges and
display-only conversation bridges are correctly separate. Exact provider references are not
silently replaced by heuristic links; auxiliary requests are explicitly provisional. The tests
cover the historical Codex/Claude cases, forks, cross-session parents, compaction, pagination,
and the dashboard/session consistency contract. This is a solid behavioral baseline.

| Priority | Finding and evidence | Consequence / recommended treatment |
| --- | --- | --- |
| P0 | `analysis/lineage.py` is ~1,360 lines and combines candidate retrieval, scoring, retrospective decisions, topology, stream affinity, auxiliary promotion, and API serialization. `db/crud.py` adds ~580 lines of graph caching and UI projections to a general CRUD module. These layers exchange deeply nested, mutable `dict` shapes. | Hard to locate an invariant or safely extend a provider. Extract typed, pure phases and a dedicated read service without changing grouping rules. |
| P0 | `GET /sessions/{id}/lineage` bypasses `_session_lineage_graph` and rebuilds a full graph; the UI polls it every 5 seconds. One exploratory local request for session `761ca1ec-…` returned ~1.4 MB and took ~8.7 seconds when called alone. This is not a controlled benchmark. | Share a revisioned graph or serve a bounded diagnostics projection; instrument cold/warm timings and payloads before choosing a final design. |
| P1 | `_session_lineage_graph` hashes every session stream hint and queries global request count/max rowid even on a cache hit; any unrelated global append changes the revision. The four-entry cache has no byte/size bound. | Polling cost grows with session length; unrelated capture can invalidate historical sessions. Define explicit evidence-revision/invalidation rules, then measure cache hit cost and memory. Do not persist lineage merely to fix this. |
| P1 | `get_session_lineage_snapshots` selects full `Request` ORM rows, including potentially large retained raw/canonical bodies, although `RequestSnapshot` uses metadata only. `_conversation_projection_view` counts blocks for all visible cards, although only group-latest comparisons need counts. | Avoidable SQLite I/O, allocations, and JSON work. Use metadata-only projection and exact comparison request IDs, retaining batched queries and a pinned read snapshot. |
| P1 | `diff_contexts` uses `SequenceMatcher(autojunk=False)` and may run for as many as 64 candidates per child. Repeated-block synthetic comparisons took about 1 ms/100 blocks, 8 ms/300, and 33 ms/600 in one local run; graph recomputation multiplies that cost. | Benchmark realistic and adversarial sessions, then precompute/reuse fingerprints, weights, and deltas or optimize alignment only after proving equal decisions. |
| P1 | The API/UI use both `capture` and `session`; `lineage_fragment_count` is actually the number of root-to-leaf diagnostic paths; `confirmed_parallel_streams` and the word “confirmed” can sound stronger than heuristic display evidence. `Inferred 80%` can read like a probability, although docs say it is an uncalibrated score. | Establish a glossary and precise labels. Add compatible API aliases if desired; avoid changing existing field meanings in place. |
| P1 | The context panel says “Context size” and the activity chart says “Input tokens” while `tokens_total_input` is the locally analyzed visible estimate. For opaque/partial context it need not equal provider-reported input usage. | Label the estimate and fidelity clearly; make the primary-number choice explicitly before changing UI metrics. Keep provider and local totals distinct. |
| P1 | Dashboard `ConversationFlows` and Session Detail `SessionConversationSequences` duplicate group headings, evidence text, density controls, and row composition. `RequestFlow` combines a card, lineage-marker tooltip, and scrolling row; the session component also owns several interacting revision/pagination/selection state maps. | Define reusable, screen-independent request/conversation UI primitives and focused state hooks before rearranging Session Detail. Keep backend analysis and page-specific fetching outside the primitives. |
| P2 | `SessionLineage` shows only 25 nodes at a time, but downloads the complete graph. An ambiguity candidate outside the visible window is labelled using the selected node as a fallback; its button can therefore display the wrong request number. SVG edges also have very small touch targets. | Fix the label from the full node index and add keyboard/touch-accessible edge selection. Keep the bounded viewport while bounding network work. |
| P2 | The default compact card hides failed/incomplete status visually (though its accessible label includes it). Detailed-mode icon explanations are mouse-hover only; the entire card is one button. The context-panel “Open request” uses a full-page anchor whereas card navigation is SPA-based. | Small status marker, keyboard/focus explanation, consistent navigation, and manual light/dark/mobile contrast and overflow QA. Keep compact cards minimal and without hover tooltips as requested. |
| P2 | Session Detail imports PDF libraries eagerly and routes are eagerly imported in `App.tsx`. This is broader than lineage but affects time-to-interactive on conversation screens. | Measure bundle/chunk impact; lazy-load the PDF export and/or route if material. Do not make this a blocker without measurement. |

### Correctness questions to investigate, not silently “fix”

- The exact-parent lookup takes a provider/response-ID match and chooses a recent row when IDs
  collide. Add a diagnostic fixture for duplicated IDs, cross-session matches, and impossible
  timestamps; decide whether to mark a collision unresolved rather than arbitrarily selecting
  a parent. Preserve legitimate external-session exact parents.
- Verify ordering for concurrent requests whose recorded start, estimated start, completion,
  and `session_seq` disagree. Inference must not use a future candidate, but an exact provider
  edge must not be discarded merely because clocks or capture completion are odd.
- Validate that each internal request appears once in the session-wide Sequence and at least
  once in the conversation/auxiliary projection; shared history may appear in multiple groups
  but must not inflate session totals. Check this as an invariant over generated graphs.
- Confirm that opaque blocks, purged content, failed requests, and context resets never become
  positive proof of a direct parent or a separate stream merely through a low candidate count.
  `Opaque changes: x` remains a count difference, not encrypted-content comparison.

## Implementation phases

### 0. Freeze the baseline and define a performance envelope

1. Preserve current synthetic behavioral fixtures as characterization tests: exact/inferred/
   ambiguous/unresolved parents, retrospective correction, Codex stream hints, provider changes,
   auxiliary promotion/rejoin, external parents, forks, compaction, session ordering, and opaque
   comparisons. Add one compact golden API fixture for each major view, excluding raw content,
   raw cache keys, and personal request data. Do not make tests depend on a local user database.
2. Instrument the read path without logging payloads: snapshot load and row/block counts,
   candidate retrieval/scoring/diff time, projection time, serialization time, query count,
   response bytes, cache hit/miss, and peak memory in a local benchmark harness. Use generated
   sessions with 100 / 1,000 / 2,000 requests, dense repeated blocks, many roots,
   forks, opaque blocks, and cross-session exact references.
3. Measure Dashboard, Conversation preview, selected-context lookup, conversation pagination,
   and full diagnostics separately, both cold and warm. Start with provisional targets at
   2,000 requests: warm dashboard/conversation/selected-context reads under 1 second p95,
   cold graph analysis under 5 seconds p95, and first usable diagnostics under 2 seconds with
   a bounded initial payload. Confirm or adjust those targets against the baseline fixture and
   a declared reference machine before treating them as a release gate. Record peak memory and
   SQL count alongside latency. The exploratory ~8.7-second diagnostics call is a reason to
   benchmark, not a reliable production percentile.

### 1. Make analysis phases and contracts explicit (behavior-preserving)

1. Move lineage-only read/serialization work out of `db/crud.py` into a focused repository and
   `SessionLineageService` (or equivalent), leaving CRUD functions as thin entry points. Keep
   one authoritative graph/revision path for Dashboard, Session Detail, and diagnostics.
2. Split `analysis/lineage.py` by responsibility: candidate index/scoring and retrospective
   decisions; graph topology; conversation/auxiliary projection; and public serialization.
   First extract code mechanically behind unchanged public functions, then simplify. Centralize
   evidence codes, thresholds, fidelity states, and group kinds in one typed contract; explain
   why each threshold exists and what negative case it protects.
3. Replace mutable cross-phase `dict` structures with small frozen dataclasses or `TypedDict`
   contracts and explicit serialization at the API boundary. Keep request IDs and group keys
   distinct types/names. Add schema/contract tests between Python responses and the TypeScript
   client; consider generated TS types if the project's tooling can support it cheaply.
4. Document invariants in code: at most one accepted direct parent per child; provider exact
   authority; no direct edge from display-only affinity; no inference from adjacency/model/
   generic agent alone; group membership may overlap only through shared ancestry; auxiliary
   is not a conversation; analysis is deterministic for the same evidence/version.

### 2. Remove measured read-path waste, then address scale

1. Load only metadata columns required for `RequestSnapshot`, not raw/canonical bodies. Keep
   the single batched block read, and add query-count and peak-allocation regression tests.
2. Narrow block-count aggregation to the selected request and accepted parent for each group;
   do not aggregate every preview card. Reuse the same backend comparison function for both
   screens, including observed-only opaque counts.
3. Make graph revision/invalidation explicit for appends, deletions, session reassignment,
   hint backfills, block/fidelity/provider-ID changes, migration, and external-parent arrival.
   Start with a lighter no-schema revision if sufficient; if not, propose a small persisted
   **evidence revision counter** with a migration, not persisted lineage output. Bound cache
   by memory as well as entry count and verify concurrent readers see one consistent revision.
4. Reuse the revisioned graph for diagnostics. If full diagnostics is still too costly or large,
   serve a lightweight path/summary index followed by a revision-bound selected-path window
   and on-demand edge detail. Keep the full response available through the current route or
   an explicit full-detail option during the client transition; no API version is needed.
   Keep the UI's currently bounded 25-node viewport and stale-revision recovery.
5. Profile candidate diff hotspots on dense/repeated histories. Precompute semantic keys and
   weights once per graph, memoize pairwise deltas during a rebuild, and consider a bounded
   alignment algorithm only with tests proving the same accepted parents and diagnostics.
   Avoid a premature persisted conversation table or incremental algorithm with different
   semantics.

### 3. Polish terminology, UX, and presentation

1. Publish one glossary for session, capture, request/invocation, direct predecessor,
   diagnostic path, lineage segment, supported conversation, provisional auxiliary activity,
   exact/inferred link, and display-only bridge. Align `docs/request-tracking-and-conversations.md`,
   `docs/development.md`, API descriptions, and UI labels. Keep historical API names as aliases
   while the existing UI/client is updated; do not introduce an API version solely for naming.
   In particular, expose `diagnostic_path_count` rather than presenting a path count as fragment
   count, retaining `lineage_fragment_count` as a compatibility alias where needed.
2. Replace probability-looking percentages with “inference score” or equivalent in detailed
   UI and tooltips. Tone down heuristic “confirmed” copy where it could imply a provider-
   verified task ID. Explain that Conversation numbers/group keys can move after new evidence
   and must not be used for durable user actions without a separate identity design.
3. Keep the locally analyzed visible-token estimate as the primary context-panel number, with
   an explicit estimate/fidelity label, especially for opaque/partial requests. Show provider-
   reported input separately when available; never combine the two into one unlabeled metric.
   Align the activity-chart label with the same semantics. Retain the concise
   `Opaque changes: x` row and its explicit count-only caveat.
4. Build the reusable UI component boundaries below before the upcoming Session screen
   rearrangement. Keep Dashboard and Session Detail as thin containers that provide data,
   loading/error states, and navigation/paging callbacks. Consolidate revision-bound pagination
   state and tests for concurrent polling, selected request, stale cursors, deep links, and newly
   promoted auxiliary chains.
5. Fix the off-window ambiguity-candidate label. Review compact status visibility; retain
   no compact hover tooltip, but make detailed explanations accessible by keyboard/focus as
   well as pointer. Improve diagnostic edge hit targets or provide an equivalent edge list.
   Verify headings, focus order, contrast, selected states, wrapping, horizontal scroll,
   320–1440px layouts, light/dark themes, and touch operation. Use consistent SPA navigation.
6. Measure initial JS before/after optional lazy loading; isolate PDF export and diagnostics
   code only if it materially improves conversation-screen startup.

#### Reusable UI component boundaries

| Layer | Responsibility and proposed extraction | Should not own |
| --- | --- | --- |
| Request card | Extract the card and lineage marker from `RequestFlow`: compact/detailed rendering, selected/focus/status states, a typed card prop, accessible explanation, and `onSelect`/`onOpen` callbacks. | API calls, URL construction, parent inference, token calculations. |
| Request sequence row | Keep a horizontally scrollable ordered list of cards and an optional trailing `More` action. Use it for the dashboard preview, session-wide sequence, grouped conversation rows, and focused diagnostic windows. | Conversation membership decisions, pagination cursors, screen headings. |
| Conversation group row | One presentational group header + evidence description + independent density control + sequence row + optional segment-index/trailing slots. Dashboard may omit the index; Session Detail may supply it. Auxiliary uses the same component with a distinct label/state. | Fetching older groups, resolving revisions, deciding whether a group is a conversation. |
| Context/activity overview | Compose the existing chart and a pure selected-request context panel; expose an action area for future compare/move controls. Responsive grid and spacing belong here, while each screen supplies selected data/actions. | Global query state or an assumption that the panel always shows the latest request. |
| View state | Keep small hooks for selection-follow-newest, session-scoped density choices, and revision-bound page accumulation. Reuse only the state machine common to both screens; keep dashboard navigation and Session Detail pagination adapters separate. | A single large hook with screen-specific branches or module-global behavior that leaks across sessions. |
| Presentation vocabulary | Centralize lineage icon/tooltip and conversation-evidence copy in typed display mappings shared by cards, group headings, and diagnostics. The backend remains the source of the evidence code and counts. | Classification, scoring, or derivation of conversation membership in TypeScript. |

Prefer explicit composition and callback props over a generic component with many booleans
(`isDashboard`, `isSession`, `showIndex`, etc.). Define a small public prop contract for each
primitive, document it next to the component, and test it with fixtures from both current
screens plus a third minimal host to prove it is actually reusable. Extract existing behavior
first; rearrange the Session screen only after these boundaries are stable. Consolidate card
widths, density, selected/focus styles, responsive wrapping, and dark/light tokens so reuse
does not produce subtly different versions of the same UI.

### 4. Production gate and rollout

- Run the full backend tests, frontend tests, TypeScript build, and UI production build. Add
  property/invariant tests and privacy-safe scale benchmarks; capture latency, SQL count,
  memory, and payload results against the agreed envelope. Add CI-friendly thresholds where
  stable, and keep larger benchmarks runnable manually.
- Replay privacy-safe synthetic cases corresponding to previously investigated sessions, and
  manually inspect a real session only read-only. Compare before/after parent states, group
  memberships, auxiliary count, ordering, and icon/explanation labels. Any intentional
  classification change must be separately documented and approved; this refactor should not
  silently reclassify existing data.
- If a schema revision counter is chosen, provide the required additive migration and explicit
  backfill/data-migration procedure per `AGENTS.md`; test upgrade from an existing database.
  If no schema change is needed, state that explicitly in the implementation PR.
- Update the user guide and a short maintainer architecture note with a pipeline map, phase
  contracts, evidence-strength rules, cache invalidation contract, performance envelope, and
  instructions for adding a new provider or evidence type.

## Product decisions recorded

1. **Compatibility:** Align terminology in the current API; do not introduce a new API version
   yet. Preserve conversation behavior unless an accuracy correction is separately reviewed.
   Where a stored field/table changes, migrate existing databases; for API response renames,
   use a compatibility alias during the client transition when practical.
2. **Scale:** Treat approximately 2,000 requests per session as the normal upper bound. Still
   fail gracefully for larger sessions, rather than assuming they cannot occur.
3. **Opaque/partial metrics:** Keep the locally analyzed visible-token estimate as the primary
   context number, clearly labelled. Provider-reported totals may be shown separately but must
   not silently replace or be conflated with the visible estimate.

No branch merge or live-database mutation is part of this plan.
