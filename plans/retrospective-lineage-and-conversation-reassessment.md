# Retrospective parent inference and conversation reassessment

## Status and scope

**Proposed; not implemented.** This plan follows the investigation of session
`300de078-a310-494c-aa75-95bf6ad2755d` and the discussion of future evidence,
ancestor competition, and conversation attribution. It changes backend analysis and,
only where needed, the shared dashboard/Session Detail presentation. It does not
authorize a branch merge, a live-database migration, or a rewrite of captured rows.

The goal is to make a later, well-evidenced continuation help correct an earlier
*inferred* parent or conversation assignment without turning an uncertain link into
an “exact” one. Exact provider links remain authoritative. A conversation remains a
supported request stream, not a claim that every adjacent card has a direct edge.

## Observed problem and current behavior

The investigated session switches from a Codex request stream to Claude Code around
request #270. Requests #270–#271 look like short Haiku/auxiliary calls; they should
not be silently treated as a direct continuation of either main sequence. #272–#284
appear to be a growing Claude Code context. #273 is inferred from #272, but #274
onward are mostly marked ambiguous. The initial investigation found that #274
scores approximately 0.968 against #273 and 0.819 against #272: the 0.148 margin
just misses the current 0.15 requirement. Later requests have multiple even closer
ancestor candidates. In #274's input, #272's output appears earlier than #273's
output, yet both are credited as promoted output. Reconfirm these observations
against read-only metadata before implementation; do not put request bodies or the
user's SQLite database in fixtures or commits.

Today `analysis/lineage.py` builds candidate parents from earlier requests in the
same session that share non-configuration block fingerprints, regardless of agent,
provider, or already projected conversation. It compares up to 64 candidates per
child. Its ordered block diff scores retained input, child coverage, and promoted
parent output, but does not test whether the promoted output is the latest relevant
turn in the child's input. A candidate must score at least 0.80 and lead the
runner-up by 0.15. A sole candidate already has a runner-up score of zero; lowering
the absolute floor merely because there is one candidate is **not** the fix.

The graph and conversation projection are already recomputed when the session's
request set changes. Later requests may change displayed path/group evidence, but
the direct-parent loop processes each child using only earlier candidates. A later
exact edge `B → C` confirms that `B` and `C` continue together; it does not by
itself prove whether `A → B` or some competing earlier edge was correct.

## Invariants

1. Preserve exact predecessor IDs, including external-session parents. Missing or
   cyclic explicit references remain `unresolved_exact`; never replace them with a
   heuristic edge. Retrospective inference can revise only heuristic/unknown links.
2. One request has at most one accepted direct parent; one parent may have several
   children. Never globally “consume” a candidate or exclude it because it was
   assigned to another fork. True forks must remain possible.
3. No parent is inferred from session adjacency, model, provider, agent label, cache
   hint, or a later request alone. A different known agent is useful *negative or
   stream-separation evidence*, not an infallible task ID. A model switch alone is
   not a conversation split.
4. Keep direct lineage, display-only stream affinity, and unassigned/auxiliary
   activity distinct. Do not show a parent-relative token delta across a display
   bridge or an uncertain association. Never label an inferred edge “exact.”
5. All classification and aggregation live in Python. Dashboard, Session Detail,
   and lineage diagnostics consume the same backend result and explain the same
   evidence. Existing request/session totals still count each stored request once.

## Implementation design

### 1. Baseline and regression fixture

- Capture a privacy-safe diagnostic for #260–#290: session sequence, times and their
  provenance, provider/agent/model/endpoint, context fidelity, explicit response
  IDs' *presence* and resolved matches, stream-hint provenance, candidate scores,
  and ordered block metadata (direction, type, position, message index, token
  count, fingerprint/tool-call ID). Do not log raw content or raw cache keys.
- Confirm which of #270–#271 are genuinely auxiliary versus merely unlinked.
  Inspect their context/response shape and temporal relationship to the main
  Claude chain. If there is no positive association, label them **unassigned**,
  not “auxiliary to Claude” as a fact.
- Build synthetic, small fixtures reproducing the relevant topology and nested
  block histories; never make tests depend on a personal live database. Record the
  before/after parent state, competing scores, and conversation groups.

### 2. Turn-frontier evidence for candidate parents

- Extend the provider-neutral block comparison in `analysis/context_diff.py` (or a
  separate lineage helper) with a *turn-frontier* measurement. Align occurrences
  in order as today, then locate the candidate's output in the child's input by
  message index/position and block type. Treat multiple thinking, assistant,
  tool-call, and tool-result blocks as one possible turn; do not require exactly
  one “next block” or assume `session_seq + 1`.
- For competing ancestor candidates `P` and `Q` where an accepted edge/path shows
  `P` precedes `Q`, identify material unique context/output contributed after
  `P`. Prefer `Q` for child `R` only if `R` retains that contribution in the
  expected order and `Q`'s promoted output occupies a later compatible turn than
  `P`'s. Shared old prefix alone must not trigger this preference. Keep sibling
  candidates competing; do not turn ancestor preference into a fork prohibition.
- Make the frontier signal explicit and inspectable: e.g. `latest_turn_promoted`,
  `ancestor_output_older`, `frontier_unavailable`, or `frontier_conflict`, with
  relevant block/message positions and a bounded score effect. Calibrate the
  score effect on positive and negative fixtures; do not silently bypass the
  existing 0.80 absolute floor or a close-runner rule. If hidden/partial/opaque
  blocks, duplicate fingerprints, compaction, or concurrent calls make the
  frontier indeterminate, retain ambiguity.
- Keep candidate search across earlier session requests. Use known agent changes
  as a guarded compatibility feature: a cross-agent *inferred* direct edge needs
  distinctive continuation evidence (such as the latest output turn), not merely
  shared boilerplate or a long copied prefix. Explicit links take precedence.
  Provider or model changes by themselves are not hard filters.

### 3. Bounded retrospective reassessment

- Build the graph in passes: resolve all exact edges; score provisional heuristic
  candidates; then revisit ambiguous and weak inferred boundaries with later
  evidence before computing final topology and conversation projection. An
  accepted exact successor chain is a strong anchor for stream membership, but
  never an automatic proof of its root's earlier parent.
- Start with a bounded look-ahead (for example, up to three accepted descendants
  within the next 64 observed requests); tune the bound with performance tests.
  Reuse candidate deltas/scores and indexed fingerprints rather than comparing
  every request with every later request. Do not let an inferred descendant that
  depends on the very disputed edge confirm itself. Reassess oldest disputed
  boundaries first, with deterministic tie-breakers and an explicit iteration
  limit so append order cannot create an unstable loop.
- Promote or replace an inferred parent only when *independent* ordered-turn
  evidence plus the anchored continuation makes the new candidate a clear winner.
  Otherwise leave the direct parent ambiguous while allowing a separately marked
  same-stream display association if supported. Keep original candidate scores,
  adjusted evidence, and reason codes visible in diagnostics so a retroactive
  change can be explained.
- Do not relax the single-candidate floor by default. Add a targeted test proving
  that a sole sub-0.80 candidate stays uncertain; revisit the threshold only after
  positive/negative fixture calibration shows a justified rule.

### 4. Conversation grouping and auxiliary/unassigned activity

- Run conversation projection **after** finalized parent decisions. A sustained
  Claude Code chain with substantive context separate from an established Codex
  chain should qualify as an independent agent stream even without A–B–A–B
  interleaving. Require both a known distinct agent identity and corroboration
  such as an accepted multi-request chain plus disjoint meaningful context;
  agent/provider name alone or a one-off call cannot create a new row. Preserve
  exact forks and existing Codex stream-affinity behavior.
- Do not insert #270–#271 into the Claude main request sequence simply because
  they are nearby or use Haiku. If positive evidence associates them with the
  Claude task, classify them as *associated auxiliary activity* with no invented
  direct edge. Otherwise leave them unassigned. If multiple supported
  conversations exist, expose unassigned/auxiliary requests in a clearly labelled
  compact “Other session requests” area, rather than smuggling them into one
  conversation row or creating a spurious conversation. Retain the single-row
  session-activity fallback when no split is supported.
- Define explicit backend group/membership/evidence fields for this presentation;
  update the shared projection in `db/crud.py` and both UI consumers together.
  Keep group keys stable where possible; on reclassification, invalidate
  revision-bound cursors and handle a selected group that has disappeared. Update
  card tooltip/diagnostic wording to distinguish `inferred`, `ambiguous`,
  `same_stream_unlinked`, `auxiliary_association`, and `unassigned`.

### 5. Cache, schema, and documentation

- Bump `ANALYSIS_VERSION` so the cached dashboard/session graph and revision-bound
  pagination cannot reuse old classification. The raw lineage endpoint and shared
  conversation endpoint must yield consistent memberships for the same request
  set. No schema migration is expected if the plan uses existing request/block
  metadata and derives associations at read time; if new persisted fields become
  necessary, follow `AGENTS.md`'s additive-column and data-migration rules.
- Update `docs/request-tracking-and-conversations.md` after the behavior is
  implemented: explain retrospective changes, what an exact successor does and
  does not prove, the turn-frontier signal, the independent-agent evidence rule,
  and the new auxiliary/unassigned presentation. Do not promote Anthropic
  `diagnostics.previous_message_id` or another diagnostic/cache field to an
  exact predecessor ID without a documented lineage contract and tests.

## Verification and acceptance

- In a synthetic reproduction of the Claude sequence, #273 remains linked to
  #272; #274 prefers #273 over #272 based on later, distinctive output in its
  input. Subsequent requests continue from the most recent supported Claude
  turn instead of becoming separate ambiguous segments, unless a fixture
  deliberately removes the necessary evidence. Diagnostics state which signal
  resolved each formerly close ancestor contest.
- A later exact `B → C → D` chain may revise `B`'s conversation membership, but
  cannot fabricate `A → B` when `B`'s own input cannot distinguish candidate
  parents. A real fork from an older ancestor remains a fork; a later sibling is
  never excluded merely because another branch was already accepted.
- The target session shows the supported Claude stream separately from the Codex
  stream. #270–#271 are either justified auxiliary activity or explicitly
  unassigned, not falsely connected to the main Claude chain. No model-only or
  provider-only split is introduced; same-agent independent tasks and copied
  contexts do not gain false direct edges.
- Cover exact/external/unresolved parents, cross-agent handoffs, overlapping
  calls, retries, duplicate hashes, multi-block turns, tool cycles, compaction,
  partial/opaque contexts, missing outputs, a sole weak candidate, and a true
  ancestor fork in `tests/test_lineage.py`. Cover dashboard/session parity,
  counts, gap explanations, row ordering, selection, and stale cursors in
  `tests/test_dashboard_stats.py` and relevant UI tests.
- Profile 500- and 2,000-request sessions with realistic growing contexts.
  Compare analysis wall time, query count, memory, and cache reuse to the
  baseline; keep bounded candidate retrieval and look-ahead. Avoid an
  all-pairs or full-raw-body scan on every live update. Run `pytest`; if UI code
  changes, run the frontend tests and `make ui` so the packaged app is current.
- Validate read-only against the observed session after implementation. Treat
  disagreement with the expected chain as a diagnostic to investigate, not a
  reason to hard-code session numbers or weaken evidence thresholds globally.

Implementation is complete when later trustworthy evidence can correct a prior
heuristic conversation assignment, the Claude ancestor-score ambiguity is
resolved by ordered-turn evidence where available, and the UI still makes every
uncertain direct-parent boundary explicit.
