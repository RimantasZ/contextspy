# Conversation stream identity across lineage gaps

## Status and relationship to earlier work

**Implemented in `session_conversation_view` (2026-09-27).** The live database has not been
upgraded; running `contextspy db-upgrade` on it still requires separate authorization. The
v6 migration and projection were validated on an isolated SQLite backup: the observed session
produced two rows, ending at #410 and #406, with display-only bridges #404→#407 and
#294→#405 and no new lineage parent edges. Migration of that copy took about 6.6 seconds;
the database file occupied about 6.6 GB, so the CLI's additional backup needs comparable
free space. The conversation presentation was subsequently refined to keep all visible
requests in one scrollable row per conversation, with each card marking its own parent
evidence; diagnostic segment boundaries remain available in the segment index. This is a follow-up to
[`session-conversations-and-lineage-views.md`](session-conversations-and-lineage-views.md).
That archived plan delivered the two Session Detail modes and a shared dashboard/session
projection. This plan changes the **Python conversation classification and display ordering**;
it is not merely a React layout adjustment. Do not merge branches or modify the user's live
database as part of planning.

## Observed failure and desired result

In session `a13949b2-bd83-4c9b-8c67-3312634487ce` (`codex 0925`), the current backend reports
one conversation because it only promotes sustained forks or sustained interleaved chains.
That rule misses a short, independently resumed Codex request stream:

| Requests | Captured evidence | Intended display |
| --- | --- | --- |
| `…402 → 403 → 404` | Exact response-predecessor chain; stream hint A | Earlier part of the main conversation |
| `405 → 406` | Exact chain; stream hint B, distinct from A | Second conversation |
| `407 → 408 → 409 → 410` | Exact chain from 407; hint A; 407 has **no recorded predecessor** | Resumption of the main conversation, with a visible lineage gap before 407 |

The two hints are the requests' `prompt_cache_key` values. In this capture, A occurs on 384
requests including 404 and 407–410; B occurs on 12 including 285–294 and 405–406. More
importantly, retained non-configuration input block hashes corroborate the two resumptions:
404→407 shares **156/156** earlier hashes (tool calls/results and other transcript blocks), and
294→405 shares **23/23**; 404→405 shares **0**. These are read-only observations from the local
SQLite database; no request bodies or key values are copied into this plan.

The latest part of the screen should therefore read approximately:

```text
Conversation with latest response #410     #410  #409  #408  #407  [lineage gap]  #404  #403  #402 …
Other supported conversation             #406  #405  [older history, if supported, behind a gap] …
```

The row whose **latest completed request** is newest is first. In this case #410's row precedes
#406's row. Neither the UI nor the API may assert an exact 404→407 or 294→405 parent edge: the
evidence supports **same display stream across a gap**, not a captured direct continuation.
Older B requests such as 285–294 may belong below 405 when the classifier confirms them; the
recent second-row example does not require hiding proven older history.

### Important limit on the cache-key signal

`prompt_cache_key` is an application-supplied cache routing/accounting key, **not** a documented
conversation or process ID. OpenAI [explicitly describes it as grouping requests for cache
reuse/accounting](https://developers.openai.com/api/docs/guides/prompt-caching). A different key
alone must not create a conversation; a shared key alone must not merge unrelated tasks.
Within this observed Codex capture it is a useful *stream-affinity hint* because exact chains
and substantive context retention independently corroborate it. The product may say
“supported separate stream,” not “proven separate Codex process.”

## Product and classification rules

1. Keep **invocation lineage** (exact/inferred primary parent edges) distinct from **conversation
   membership** (which display stream a request belongs to). A conversation can contain several
   disconnected lineage segments. `session_seq`, timestamps, agent=`codex`, model, and a path
   boundary are not sufficient evidence of a split or a parent edge.
2. Preserve existing exact/inferred continuation rules and structural fork diagnostics. A
   provider-predecessor edge is strong same-stream evidence; an exact edge across different
   cache hints takes precedence over the hint and is treated as hint rotation/conflict, not an
   automatic split.
3. Add a provider-neutral, source-labelled **stream-affinity hint** to request snapshots.
   For OpenAI Responses/Codex, extract a well-formed `prompt_cache_key` at capture. Hash the
   value before persistent storage; keep its provenance (`openai_prompt_cache_key`) and never
   expose the raw key in API responses or UI. Do not infer identity from response-ID prefixes.
4. In Python, form same-stream *affinity* links across lineage gaps only when the hint agrees
   **and** substantial non-configuration transcript/context evidence agrees. Reuse the existing
   block fingerprint/diff machinery, with tested minimum distinct-block/weight and retention
   thresholds; do not count repeated system prompts or tool definitions as corroboration.
   Such a link may assign group membership and explain a gap, but must not be added to the
   accepted `context_continuation` edge set or used for a parent-relative token delta.
5. Promote a separate display group when a distinct hint is supported by an independent
   accepted chain (at least two requests here) or similarly strong task/stream evidence **and**
   it is inconsistent with the established stream's substantive context. The 405→406 chain
   qualifies against the 404/407 stream; a single key-changing call does not. Preserve the
   existing proven-fork and sustained-parallel-chain routes when no hint is available.
6. Unknown, transient, guardian-prefixed, missing, or conflicting hints stay uncertain unless
   corroborated. Do not make a third row merely because 282–286 contain short-lived different
   keys. Do not force every unassigned fragment into a claimed exact chain. Existing shared
   fork ancestry may appear in multiple groups and is marked as shared; session totals still
   count each stored request once.
7. Classify all requests before paging, using a deterministic stable group key anchored in
   durable evidence or a request ID rather than a rank/ordinal. Expose group evidence and
   bridge/gap provenance (`exact`, `inferred`, `context_affinity`, `hint_conflict`, `unassigned`)
   so both screens use the same vocabulary. Do not leak key hashes to clients unless strictly
   necessary; an evidence label is sufficient.

## Backend and migration work

- Extend `Request` in `db/models.py`, the capture path in `proxy/addon.py`, and
  `RequestSnapshot`/snapshot loading in `db/crud.py` with the source and digest of the stream
  hint. Extract from the decoded/canonical request before raw bodies are purged. Keep analysis,
  grouping, and evidence decisions in `analysis/lineage.py` and the shared Python projection,
  never in TypeScript.
- Add the new columns to `db/database.py:_migrate()`. Because existing captures need this
  derived value, bump `SCHEMA_VERSION` and add an idempotent, **batch-iterated** data migration
  in `db/migrations.py` that reads retained canonical/raw JSON, validates the key, and stores
  only the digest/provenance. The current migration runner commits only after all versions
  finish; if interruption-safe partial commits are needed, add explicit progress checkpoints
  to that runner rather than calling a single transaction “resumable.” Rows whose bodies were
  already purged remain hint-unknown and use context/lineage fallback. Do not repeatedly parse
  multi-megabyte bodies on every dashboard poll. Account for `startup_vacuum` purging both raw
  and canonical bodies.
- The observed database is approximately 6.6 GB. Test migration on a copy and measure time,
  disk/backup requirements, and interruption behavior before recommending `contextspy
  db-upgrade` on the user's live database. Running that command requires the user's separate
  authorization; this plan does not authorize it.
- Bump `ANALYSIS_VERSION` and ensure graph-cache/revision invalidation covers hint backfill and
  reanalysis, not only a changed request count/rowid. Existing session and dashboard APIs must
  return the same group keys/membership for the same revision. Keep lineage diagnostic paths
  and their parent-edge semantics unchanged.
- Review candidate retrieval and indexing so 500/2,000-request sessions remain bounded.
  The new affinity comparison should use indexed fingerprints or bounded candidate sets, not
  all-pairs raw-body comparisons.

## Ordering, pagination, and UI work

- Change the shared group ordering from “primary first, then newest secondaries” to **all groups
  by latest completed-request timestamp descending**, with a deterministic tie-breaker.
  The dashboard's four-group preview must select the four most recently active groups, not
  reserve a slot for an older primary. Session Detail can still page through every group.
  Keep stable opaque group keys, selected-group context state, and revision-bound cursors when
  rows reorder after a live append.
- Within each group keep newest-first requests and backend-defined diagnostic lineage segments,
  but render visible cards in one scrollable row per conversation. The main row should show
  #410, #409, #408, #407, then #404, #403, #402 as its history is revealed; #407's card marks
  **same stream; direct predecessor not established**. The second row begins #406, #405;
  #405's card carries the same marker if older B history is included. Exact/inferred parent
  types and unresolved parents likewise appear on individual cards. No edge may be drawn
  across either gap.
- Keep the selected group's context-size panel parent-relative: #410 compares to #409, #406
  to #405. At a gap root such as #407 or #405, show no parent delta unless the existing lineage
  analyzer independently resolves a parent. Diagnostic mode continues to show the raw exact
  and inferred graph, not invented conversation bridges.
- Update evidence labels and tests in `ConversationFlows`, `SessionConversationSequences`,
  `RequestFlow`, and the dashboard selection/fallback logic. “Primary” may remain an internal
  anchor but must not imply top-row order; consider neutral user-facing group labels.

## Verification and acceptance

Use a small synthetic fixture with the observed topology and fingerprint relationships; do
not commit real request bodies or the live SQLite database. Verify:

- Exactly two supported display streams in the target session after available hints are
  backfilled; #405/#406 belong together, #404 and #407–#410 belong together, and the row ending
  at #410 is first in Session Detail **and** the dashboard.
- #404→#407 and, if assigned, #294→#405 are **membership bridges with visible gaps**, not new
  lineage edges or context-parent comparisons. #405→#406 and #407→#408→#409→#410 retain their
  exact edges. Older secondary history is accessible through group pagination.
- #178's structural branch remains unconfirmed unless independent positive evidence appears.
  Same cache key across unrelated contexts, key rotation on an exact chain, one-off distinct
  keys, missing/purged bodies, and ambiguous/opaque context do not create false rows.
- Tests cover migration/backfill and retention, conflict precedence, dashboard/session parity,
  group ordering, live reordering and selected-key preservation, revision/cursor invalidation,
  null sequence numbers, shared ancestry, external parents, query count, and 500/2,000-request
  performance. Run the full Python suite; after UI changes run Vitest and build the packaged UI.
- Manual QA against the observed session shows the two intended recent rows and a visible gap,
  and diagnostics still reports the raw paths. Check narrow widths for no page-wide overflow.

Done when conversation rows represent supported request **streams** rather than only sustained
graph forks, while the visualization never invents a parent edge across a lineage gap.
