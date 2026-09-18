# Request lineage and context evolution plan

## Status and relationship to existing plans

The first-release vertical slice (phases 1–6) is implemented on the
`refactor-request-sequence-tracking` branch. It includes capture-safe numbering and timing,
provider-exact and conservative inferred continuation edges, parent-relative context diffs, the
capture lineage API, and the graph/timeline UI. Phases 7–9 remain follow-up work: orchestration
overlays require observable agent/trace metadata, and inferred relations intentionally remain
read-time results until real-capture accuracy and performance have been validated.

It extends, and partially corrects, `SESSION_ANALYSIS_PLAN.md`:

- the current session-analysis plan assumes requests form one sequence;
- this plan treats a capture as a directed acyclic graph of invocations;
- blocks are introduced relative to a request's parent, not merely at the first request anywhere
  in the capture;
- context continuation and agent orchestration are represented as different relationships.

It preserves the primary contract from `WEBSOCKET_INVOCATION_NORMALIZATION_PLAN.md`:

- one `Request` row is one provider invocation attempt;
- provider-declared predecessor IDs are authoritative;
- connection reuse, timestamps, capture adjacency, and session order never prove lineage;
- incomplete, partial, compacted, or opaque contexts must remain explicit rather than being
  reconstructed speculatively.

This plan does not require the unfinished block-to-JSON navigation work. Lineage and context diffs
can be implemented from the block metadata already stored today. `json_path` remains useful for a
later UI that reveals changed blocks inside canonical JSON.

## User outcome

Within one named profiling capture, users can see:

- which LLM invocations are unrelated roots;
- which invocation continued from which earlier invocation;
- where one context forked into parallel branches;
- which relationships are exact and which are inferred;
- what persisted, was added, was removed, or changed along every continuation edge;
- when requests overlapped in real time;
- possible delegation to a subagent and later contribution back to another branch, without
  presenting heuristic causality as fact;
- stable links from every graph node to the existing request detail page.

The result is a graph, not a repaired total sequence:

```text
Capture #1 -> #2 --+--> #4
                   `--> #3 -> #6
                              ... contribution --> #7

Capture #5                              unrelated root
```

`#1`, `#2`, and so on remain capture-local labels. The edges carry semantic ordering.

## Terminology and compatibility

Use these terms in new product copy and documentation:

| Concept | User-facing term | Notes |
| --- | --- | --- |
| User-controlled named time window | Capture | Existing database/API name remains `Session` initially. |
| One provider request/response attempt | Invocation | Existing database/API name remains `Request`. |
| Family of evolving contexts | Context lineage | A connected component of primary continuation edges. |
| Path created by a fork | Branch | Derived from graph shape; not stored as an edge type. |
| Explicit provider-side grouping | Conversation | Only use when an actual provider conversation ID exists. |
| Explicit agent execution grouping | Agent run or trace | Only use when observable metadata exists. |

Avoid using `thread` as the central model. It is overloaded across chat UIs, operating-system
threads, and agent products. Avoid calling an inferred lineage an "LLM session" because the proxy
cannot always observe a provider or agent session boundary.

Compatibility rules:

- keep the `sessions` table, `session_id`, `/api/sessions`, and existing CLI commands in the first
  release;
- update visible copy from generic "Session" to "Capture" where this does not make existing CLI
  instructions inconsistent;
- optionally add `contextspy capture start|end|list` as aliases later, keeping `session` commands
  working;
- do not rename `Request` rows or routes; "invocation" is explanatory product language.

## Core semantic model

### Nodes

Every existing `Request` row is one graph node. Its UUID is the stable identity and continues to
back `/requests/:id` links.

### Primary context parent

An invocation has at most one primary context parent. This edge means:

> The child invocation's effective input context evolved from this parent's effective context.

The edge is authoritative when declared by a provider predecessor ID. Otherwise it may be inferred
from context blocks when there is one sufficiently strong and unambiguous candidate.

This single-primary-parent rule keeps context deltas well-defined. A later request may incorporate
material from other branches through separate contribution edges without pretending its complete
context has multiple primary parents.

### Secondary causal edges

Represent orchestration separately:

- `delegation`: one invocation or agent run caused work to begin in another context;
- `contribution`: output from one invocation was incorporated into a later invocation whose
  primary context parent is elsewhere.

These may be exact when trace/tool metadata proves them. Content-only evidence must be labelled
inferred or suggested.

### Derived graph concepts

- A **root** has no resolved primary context parent.
- A **fork** is a node with more than one primary continuation child.
- A **branch** is a path chosen after a fork.
- A **context lineage** is a connected component using primary continuation edges only.
- Delegation and contribution edges are overlays and do not merge context lineage identity.
- Graph depth is derived from the root. It is not a global sequence number.

The graph must remain acyclic. Invalid provider data or an inference that would create a cycle is
reported as a diagnostic and is not accepted as a graph edge.

## Mapping to current data

### Request metadata already available

| Current field | Lineage use |
| --- | --- |
| `id` | Stable node ID and UI link. |
| `session_id` | Capture membership. |
| `session_seq` | Existing capture/persistence ordinal; never semantic lineage. |
| `timestamp` | Current completion/persistence time. |
| `duration_ms` | Approximate start time for legacy rows and duration visualization. |
| `provider`, `model`, `endpoint` | Compatibility/evidence features, never sufficient alone. |
| `agent` | Agent product family, not an agent/subagent instance. |
| `provider_response_id` | Exact provider node identifier when available. |
| `predecessor_response_id` | Exact provider-declared predecessor when available. |
| `context_fidelity`, `context_notes` | Prevent overconfident inference around partial/opaque state. |
| canonical request/response bodies | Optional detailed re-analysis while retained. |

An exact provider continuation can already be resolved with:

```text
parent.provider = child.provider
and parent.provider_response_id = child.predecessor_response_id
```

If the predecessor was captured outside the selected capture, return it as an external parent stub
instead of discarding the exact relationship. If only the response ID is known, return an unresolved
predecessor stub.

### Block metadata already available

| Current field | Diff/inference use |
| --- | --- |
| `request_id` | Owning graph node. |
| `direction` | Separates request input from provider output. |
| `position` | Ordered comparison within each direction. |
| `message_index` | Local structural hint; not a cross-request identity. |
| `block_type` | Role-sensitive comparison and configuration/transcript partitioning. |
| `category` | Delta token summaries. |
| `content_hash` | Exact-content matching even after retained text is purged. |
| `token_count` | Added/removed/persisted cost summaries. |
| `tool_name`, `tool_call_id` | Strong tool continuation and contribution evidence. |
| `attrs` | Provider-specific structural hints. |

No new block column is required for the first lineage release.

### Current semantics that must not be reused unchanged

`first_seen_session_seq` is capture-global: it finds the minimum `session_seq` for a content hash
anywhere in the capture. With sibling branches, that can claim a block was already seen on a branch
where it has no ancestor occurrence.

Keep the existing field for backward compatibility, but add graph-aware derivations:

- `present_in_parent`;
- `introduced_relative_to_parent`;
- `first_seen_on_ancestor_path`;
- `promoted_from_parent_output`.

The new lineage view must use parent-relative or ancestor-path semantics, not capture-global
first-seen semantics.

## Request numbering and timing

### Existing number

Retain `session_seq` as an immutable capture-local label. Rename its UI label from "Session
sequence" to "Captured request #" or simply "Capture #".

Do not use it to infer a parent. Today it is allocated when the completed invocation is persisted,
so concurrent requests can be numbered in completion order rather than start order.

### Timing enhancement

Add a nullable `started_at` column to `Request` for new captures:

- HTTP: record when the request hook observes the outgoing LLM request;
- WebSocket: record when a registered protocol accepts the invocation-opening client message;
- `timestamp` remains the completion/persistence timestamp for compatibility;
- expose `completed_at` in the API as an alias of `timestamp` while retaining `timestamp`;
- for historical rows, leave `started_at` null and optionally return a clearly labelled
  `estimated_started_at = timestamp - duration_ms`;
- never silently present an estimated timestamp as observed.

Capture membership should be resolved at request start and carried with the in-flight invocation.
Otherwise a request that begins inside a capture and finishes after it ends can be assigned to the
wrong capture or to no capture.

### Concurrency safety

Add a unique constraint/index for non-null `(session_id, session_seq)` and make sequence allocation
safe for concurrent writers. Use a transaction-safe capture counter or retry allocation on a unique
constraint conflict. Do not depend on two writers observing the same `max(session_seq)` safely.

Historical request numbers remain unchanged.

### Display ordering

For a capture graph:

1. use observed `started_at` when present;
2. use estimated start only for visual placement, with an estimated marker;
3. fall back to `timestamp`, `session_seq`, and `id` for deterministic ordering;
4. use graph edges, not timestamps, for semantic predecessor order.

Do not persist friendly paths such as `A.3b`. New edges or late-arriving parents can change those
labels. Derive lineage number, depth, and branch labels per graph response. Request UUIDs and
`session_seq` remain stable.

## Context block identity

### Exact fingerprint

Do not compare requests as sets of raw `content_hash` values. Construct a semantic fingerprint from
existing fields:

```text
(
  direction,
  block_type,
  content_hash,
  tool_name,
  tool_call_id,
  selected stable attrs
)
```

Rules:

- treat the fingerprint as role-sensitive; identical text in a user message and tool result is not
  the same semantic occurrence;
- compare input and output sequences separately;
- retain duplicate occurrences by pairing a fingerprint with its occurrence ordinal;
- do not use `message_index` as identity because indexes can shift after insertion or compaction;
- include only documented stable `attrs`; ignore volatile provider metadata;
- a content-less block can match only through strong structural identifiers such as
  `tool_call_id`, never through an empty hash.

### Context partitions

Compare different parts of the context according to their semantics:

- **configuration**: system/developer instructions and tool definitions;
- **transcript**: user/assistant messages, tool calls, and tool results in order;
- **current/injected input**: newly supplied user content or tool output;
- **output**: provider response blocks that may be promoted into the child's transcript.

Tool definitions may be compared by tool name and content rather than strict list position.
Transcript blocks require order-preserving comparison.

## Parent-relative context diff

Implement the analysis in a new backend module such as:

```text
contextspy/analysis/lineage.py
contextspy/analysis/context_diff.py
```

The frontend must not reimplement matching, scoring, token totals, or introduction semantics.

For a candidate parent `P` and child `C`, compute:

```text
P.input versus C.input
P.output versus C.input
```

Return occurrence-level mappings:

- `persisted`: parent input occurrence -> child input occurrence;
- `promoted`: parent output occurrence -> child input occurrence;
- `added`: unmatched child input occurrence;
- `removed`: unmatched parent input occurrence;
- `replaced`: matched logical configuration slot whose content changed;
- `reordered`: optional diagnostic, not automatically equivalent to persistence;
- `unobservable`: hidden/purged/opaque material that cannot be compared.

Use an order-preserving algorithm for transcript blocks, initially longest common subsequence or an
equivalent sequence matcher over semantic fingerprints. Match strong tool IDs before general
sequence matching. Match changed configuration slots separately:

- system/developer instruction by semantic slot;
- tool definition by tool name;
- other configuration by documented provider-neutral key.

Return summaries by category and block type:

```json
{
  "persisted": {"blocks": 42, "tokens": 18320},
  "promoted": {"blocks": 2, "tokens": 610},
  "added": {"blocks": 3, "tokens": 940},
  "removed": {"blocks": 0, "tokens": 0},
  "replaced": {"blocks": 1, "tokens_before": 120, "tokens_after": 145}
}
```

Expose block IDs and mappings, not duplicated content, in the graph response. Fetch retained block
content lazily through existing request/block APIs.

## Parent inference

### Evidence priority

Apply evidence in this order:

1. Provider-declared predecessor ID: exact context parent.
2. Explicit normalized agent parent/trace metadata: exact or provider-defined relationship.
3. Unique tool-call/result orchestration metadata: exact when the adapter contract guarantees it.
4. Context-diff inference: accepted only above a conservative threshold with a clear margin.
5. Similarity without a clear winner: ambiguous candidates, not a parent edge.

Exact lineage is preserved even if a context diff is small because compaction or provider-managed
state may make the visible context partial or opaque. The edge evidence should then explain the
observability limitation.

### Candidate retrieval

Do not compare every request only with the immediately previous request. For an unlinked child:

1. search earlier requests in the same capture;
2. use the existing `blocks.content_hash` index to retrieve candidates sharing rare transcript or
   output blocks;
3. include candidates connected through matching `tool_call_id` values;
4. consider provider/model/agent compatibility as weak supporting evidence only;
5. use time only to exclude impossible future candidates and bound work, never as positive proof;
6. do not infer cross-capture context parents in the first release.

Shared system prompts and tool definitions are weak evidence. Weight fingerprints by inverse
document frequency within the capture so boilerplate present in every agent request contributes
little, while rare conversation blocks contribute strongly.

### Suggested score components

Compute a normalized score from available evidence:

- ordered retention of parent transcript in child input;
- promotion of parent output into child input;
- matching tool call/result IDs;
- plausible append-at-tail structure;
- retention or deliberate replacement of configuration;
- penalties for unexplained middle mutations, contradictory structure, or low-fidelity data.

Normalize weights when some evidence, such as visible parent output, is unavailable. A request must
not receive a high score solely because it shares configuration boilerplate.

Start with deliberately conservative acceptance criteria, for example:

- best score at least `0.85`;
- margin over the second candidate at least `0.15`;
- at least one strong non-configuration signal;
- no cycle and no contradictory exact predecessor.

Treat these as versioned algorithm constants to calibrate against real captures, not user-facing
configuration in the first release.

### Result states

Every node has one of:

- `exact`: primary parent resolved from authoritative metadata;
- `inferred`: one candidate passed the acceptance threshold and margin;
- `ambiguous`: plausible candidates exist but none can be selected safely;
- `root`: no credible context parent;
- `unresolved_exact`: an authoritative predecessor ID exists but its request was not captured;
- `unavailable`: retained metadata is insufficient for inference.

Do not silently turn `ambiguous` or `unavailable` into `root` in diagnostics, even if the graph
renders them as root nodes within the selected capture.

## Delegation and contribution inference

Run causal/orchestration analysis only after the primary context graph is built.

### Exact signals

Add a provider/agent-neutral extension point for adapters or capture integrations to emit normalized
orchestration hints:

```text
agent_run_id
trace_id
parent_trace_id
delegation_call_id
contribution_call_id
provider_conversation_id
```

Do not hard-code Codex-specific field names or tool names in generic analysis modules. A registered
extractor translates observed provider/agent metadata into the normalized hint model.

Persist normalized identifiers only when actually observed. Missing values mean unknown, not a
synthetic identity based on the `agent` product name.

### Content-based signals

Potential contribution evidence includes:

- rare output blocks from one lineage appearing as newly added input in another;
- a tool result linked to an earlier cross-lineage tool call;
- an exact delegation prompt appearing as the initial task of a new root.

Content overlap alone should produce a `suggested` causal relationship, not an exact delegation.
General prose similarity is not sufficient in the first implementation.

## Target persistence model

The first release can derive exact and inferred context edges on read, allowing the scoring model to
be validated without a data migration. Once behavior is validated, add a cache/audit table:

```text
request_relations
  id                    INTEGER PRIMARY KEY
  source_request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE
  target_request_id     TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE
  relation_type         TEXT NOT NULL
                          -- context_continuation | delegation | contribution
  certainty             TEXT NOT NULL
                          -- exact | inferred | suggested
  evidence_source       TEXT NOT NULL
                          -- provider | agent | tool | context_diff
  confidence            REAL
  is_primary            INTEGER NOT NULL DEFAULT 0
  evidence               TEXT
  algorithm_version     TEXT NOT NULL
  created_at             DATETIME NOT NULL
```

Constraints and indexes:

- unique `(source_request_id, target_request_id, relation_type, evidence_source)`;
- index `source_request_id` and `target_request_id`;
- at most one primary `context_continuation` per target;
- `source_request_id != target_request_id`;
- confidence is null for evidence that is exact but not probabilistic, otherwise `0..1`;
- evidence JSON contains block IDs, counts, scores, and reason codes, not copied prompt content.

Do not initially persist lineage IDs, roots, depths, branch labels, or connected components. Derive
them from accepted primary edges so late data and algorithm upgrades cannot leave stale topology.

Persisted inferred rows are a versioned cache, not immutable truth. Recompute or replace only rows
owned by an older `algorithm_version`; never overwrite exact provider/agent evidence with heuristic
results.

## API design

### Capture lineage graph

Add:

```text
GET /api/sessions/{session_id}/lineage
```

Suggested response shape:

```json
{
  "capture": {
    "id": "...",
    "name": "...",
    "is_active": false
  },
  "analysis_version": "lineage-v1",
  "nodes": [
    {
      "request_id": "...",
      "session_seq": 7,
      "started_at": "...",
      "started_at_source": "observed",
      "completed_at": "...",
      "duration_ms": 2400,
      "provider": "openai_chatgpt",
      "agent": "codex",
      "model": "gpt-5",
      "context_fidelity": "complete",
      "lineage_key": "root-request-uuid",
      "lineage_number": 1,
      "depth": 3,
      "parent_state": "inferred"
    }
  ],
  "edges": [
    {
      "source_request_id": "...",
      "target_request_id": "...",
      "relation_type": "context_continuation",
      "certainty": "inferred",
      "confidence": 0.94,
      "evidence": {
        "reason_codes": ["ordered_context_retained", "parent_output_promoted"],
        "candidate_margin": 0.22
      },
      "delta": {
        "persisted_blocks": 42,
        "persisted_tokens": 18320,
        "promoted_blocks": 2,
        "promoted_tokens": 610,
        "added_blocks": 3,
        "added_tokens": 940,
        "removed_blocks": 0,
        "removed_tokens": 0,
        "replaced_blocks": 1
      }
    }
  ],
  "unresolved_predecessors": [],
  "ambiguous_candidates": []
}
```

Requirements:

- load requests and blocks in bulk; no per-request N+1 queries;
- return all requests in the capture, without the current 500-row request-list limit;
- keep graph payloads metadata-only and fetch full block content lazily;
- use request UUIDs for links;
- keep deterministic ordering for snapshots and tests;
- expose external parent stubs without leaking requests from a capture the caller did not request
  in future multi-user deployments.

### Edge detail

Add:

```text
GET /api/requests/{child_id}/context-diff?parent_id={parent_id}
```

Return complete occurrence mappings and category/block-type summaries. Validate that the selected
parent is either an accepted relation or an explicitly requested comparison visible to the user.
Do not return duplicated block content by default.

### Existing request/block APIs

- Keep `session_seq` and `first_seen_session_seq` for compatibility.
- Add graph-aware fields only to the new endpoints initially.
- Add parent/children navigation summaries to request detail after the graph API is stable.
- Invalidate lineage queries on the existing `new_request`, session-start, and session-end events.

## Backend architecture

Suggested provider-neutral types:

```text
BlockFingerprint
BlockOccurrence
ContextSnapshot
ContextDelta
ParentCandidate
LineageEdge
LineageGraph
OrchestrationHint
```

Suggested modules:

```text
contextspy/analysis/context_diff.py
contextspy/analysis/lineage.py
contextspy/analysis/orchestration.py
```

Responsibilities:

- `context_diff.py`: fingerprints, occurrence matching, promotion, additions/removals/replacements,
  and token/category summaries;
- `lineage.py`: exact predecessor resolution, candidate retrieval/scoring, cycle checks, roots,
  depths, branches, and deterministic graph output;
- `orchestration.py`: normalized trace/delegation/contribution evidence and extension registration;
- `db/crud.py`: bulk data loading and optional relation persistence only;
- API routers: validation and serialization only;
- frontend: graph layout, interaction, and formatting only.

The analysis modules must not import WebSocket protocol/session classes or Codex-specific payload
types.

## UI and visualization

### Capture detail integration

Add a `Timeline | Lineage` view switch to the capture detail page. Retain the request table as a
fallback and as an accessible alternative to the graph.

### Graph design

- horizontal axis: observed/estimated start time;
- node width or attached bar: request duration;
- vertical lanes: context lineages and branches;
- solid edge: exact continuation;
- dashed edge: inferred continuation;
- faint/dotted edge: suggested delegation or contribution;
- broken incoming marker: unresolved exact predecessor;
- root badge: no credible parent;
- ambiguity badge: several plausible parents;
- node color: context size or selected token category, using existing semantic palette;
- edge badge: concise delta such as `+3 / -1 blocks`, `+940 tokens`;
- overlapping duration bars provide the visual cue for real parallelism.

Changes are properties of edges, not nodes. Selecting:

- a node opens a compact invocation summary and a link to the existing request workbench;
- a continuation edge opens the parent-relative context diff;
- a causal edge shows the evidence and clearly labels exact/inferred/suggested certainty.

### Stable live behavior

For active captures:

- preserve node selection, pan/zoom, and expanded inspectors during refresh;
- add new nodes without unnecessarily reshuffling established lanes;
- allow a new exact predecessor to replace an earlier inferred edge visibly and safely;
- do not animate large topology changes in a way that implies requests moved in real time;
- show when analysis is provisional because an invocation is still in flight or a parent is not yet
  persisted.

### Accessibility and scale

- provide a keyboard-navigable tree/table representation of roots and children;
- expose edge certainty and delta text to screen readers;
- virtualize or progressively render large captures;
- offer filters for lineage, agent product, model, exact/inferred relationships, and roots;
- do not require color alone to distinguish evidence types.

## Migration and historical data

### Additive schema changes

When timing work lands:

- add `requests.started_at` through `db/database.py`'s additive migration mechanism;
- add any normalized trace/conversation identifiers only after at least one extractor uses them;
- add the concurrency-safe sequence constraint/counter with the required versioned migration;
- add `request_relations` through `Base.metadata.create_all()` plus a versioned migration only when
  historical relation caching/backfill is enabled.

### Historical behavior

- preserve existing `session_seq` values;
- do not manufacture observed start timestamps;
- optionally derive estimated starts at read time from `timestamp - duration_ms`;
- exact provider relationships can be resolved from the IDs already backfilled by current
  migrations;
- content inference can operate from retained block hashes even when `block_contents.content` and
  canonical bodies have been purged;
- rows without sufficient hashes/IDs remain `unavailable` or `ambiguous`;
- never use timestamp adjacency as a historical backfill shortcut.

### Relation backfill

After the inference algorithm is validated:

1. back up the database through the existing migration backup flow;
2. insert exact provider edges first;
3. process each capture independently for inferred primary parents;
4. process roots and accepted parent chains in deterministic order;
5. validate acyclicity after every accepted inferred edge;
6. store the algorithm version and compact evidence;
7. retain ambiguous candidates only in diagnostics unless a future review UI needs persistence;
8. make the operation idempotent and safe to rerun.

## Performance strategy

The capture analysis endpoint must not issue one block query per request.

Initial implementation:

- one query for capture requests;
- one query for all input/output block metadata;
- one query for exact external predecessors when needed;
- in-memory indexes by request, content hash, and tool call ID;
- rare-hash candidate retrieval before full sequence comparison;
- exact diff only for accepted edges and a small set of top ambiguous candidates.

For very large captures:

- cap the number of candidate parents per rare-hash retrieval stage;
- cache fingerprints and accepted edge summaries by `algorithm_version`;
- lazy-load occurrence-level mappings;
- report that analysis is partial if configured safety limits are reached rather than silently
  treating unexamined requests as unrelated.

## Failure and uncertainty states

Handle these independently:

- predecessor response ID exists and resolves: exact edge;
- predecessor response ID exists but was not captured: unresolved exact edge;
- predecessor content was purged but block hashes remain: exact/inferred diff remains possible;
- parent response body or output blocks are missing: edge can remain exact, promotion evidence is
  unavailable;
- compacted/encrypted context: exact edge with opaque delta;
- several candidates tie: ambiguous, no primary inferred edge;
- only boilerplate matches: root/unavailable, not inferred continuation;
- provider metadata creates a cycle: diagnostic, no accepted cyclic edge;
- legacy timing unavailable: deterministic placement without pretending it is exact;
- request still in flight: absent until persisted in the first release, or provisional only if a
  later in-flight node API is deliberately added.

## Test plan

### Request numbering and timing

1. Requests with an active capture retain unique `session_seq` values.
2. Concurrent persistence cannot create duplicate capture numbers.
3. Requests without a capture retain null sequence values.
4. Capture membership is taken at invocation start, even if the capture ends before completion.
5. Two overlapping requests expose correct observed starts and independent completion times.
6. Historical rows expose estimated timing only with an estimated marker.

### Exact lineage

1. One provider predecessor resolves to one primary parent.
2. Two children of one predecessor produce a fork.
3. Interleaved pooled connections do not affect exact edges.
4. A predecessor in another capture produces an external parent stub.
5. A missing predecessor produces `unresolved_exact`, not an inferred adjacent parent.
6. Provider compaction preserves the exact edge and marks the delta opaque/partial.
7. Duplicate provider IDs follow the existing deterministic provider-scoped resolution rules.
8. Malformed cyclic IDs are reported and excluded from accepted topology.

### Context diff

1. Repeated parent input is marked persisted.
2. Parent assistant output reappearing in child input is marked promoted.
3. A new user message is marked added.
4. Removed/truncated history is reported.
5. A changed system prompt is reported as replacement, not unrelated addition/removal.
6. Tool definitions match by semantic identity when order changes.
7. Duplicate identical messages remain separate occurrences.
8. Identical text in different roles does not match incorrectly.
9. Purged content still matches through hashes.
10. Content-less blocks do not match without structural IDs.
11. Token/category summaries exactly equal the mapped block records.

### Inference

1. A normal sequential chat chooses the true previous context parent.
2. Interleaved branches choose their respective parents rather than adjacent capture rows.
3. Two children sharing one parent create an inferred fork.
4. Parallel unrelated agents sharing system/tools remain separate roots.
5. Rare conversation blocks outweigh ubiquitous instructions/tool definitions.
6. Matching tool result/call IDs provide strong continuation evidence.
7. Two near-equal candidates produce `ambiguous`.
8. Low-overlap requests produce roots/unavailable states.
9. A candidate that would create a cycle is rejected.
10. Repeated analysis with the same version is deterministic.
11. Exact provider lineage always wins over a higher heuristic score.

### Branch-aware introduction

1. A block introduced on sibling A is still new when independently introduced on sibling B.
2. A block present on an ancestor path is not new on its descendant.
3. Parent output promoted into child input is distinguished from unrelated added context.
4. Capture-global `first_seen_session_seq` remains unchanged for compatibility.

### Delegation and contribution

1. Explicit normalized parent-trace metadata produces an exact delegation edge.
2. A subagent with a different context is not mislabelled as a context continuation.
3. Output incorporated into another branch produces a contribution suggestion or exact edge based
   on evidence strength.
4. Shared generic prose does not create a causal edge.
5. Causal overlay edges do not merge context lineage components.

### API

1. Captures with more than 500 requests return complete graphs.
2. Graph queries use bounded bulk database work with no N+1 block loading.
3. Node and edge ordering is deterministic.
4. Missing captures return 404.
5. External and unresolved parents do not leak unrelated request bodies.
6. Edge detail rejects invalid parent/child selections.
7. Purged and opaque states remain distinguishable.

### Frontend

1. Exact, inferred, suggested, ambiguous, root, and unresolved states render distinctly.
2. Forks and parallel time intervals render correctly.
3. Selecting a node links to the correct request UUID.
4. Selecting an edge shows the backend-provided delta without recomputing it.
5. The accessible tree/table exposes the same topology and certainty.
6. Live updates preserve user selection and viewport.
7. Mobile and large-capture fallbacks remain usable.

## Real-capture validation matrix

Validate on database copies containing:

- a simple Anthropic Messages conversation with full resent history;
- OpenAI Chat Completions with sequential tool calls;
- OpenAI Responses with explicit `previous_response_id`;
- Codex WebSocket requests with pooled connections and interleaved response chains;
- a real main-agent/subagent fork if observable;
- two unrelated agents sharing the same system prompt and tool set;
- a branch that compacts or truncates context;
- a capture spanning an invocation that starts before `capture end` and completes afterward;
- retained metadata whose raw/canonical bodies and block content text have been purged.

For each capture, manually label the expected primary graph and compare:

- exact parent accuracy;
- inferred parent precision and ambiguity rate;
- false links between unrelated roots;
- block/token delta correctness;
- graph stability across repeated analysis;
- payload size and endpoint latency.

Prefer precision over recall: an ambiguous or disconnected node is better than a confident false
lineage edge.

## Implementation phases

### Phase 1 - Establish terminology and exact graph contract

- Document Capture, Invocation, Context lineage, Branch, Conversation, and Agent trace.
- Add provider-ID-based parent/child resolution in a pure backend lineage module.
- Add graph types and deterministic topology calculation.
- Add backend fixtures for chains, forks, missing predecessors, and cross-capture parents.
- Do not add heuristic inference or persistence yet.

Acceptance: an OpenAI Responses capture renders the correct exact chain/fork topology without using
timestamps, connection identity, or adjacency.

### Phase 2 - Correct capture timing and numbering semantics

- Capture `started_at` and capture membership at invocation start.
- Keep `timestamp` as the compatibility completion timestamp.
- Make `session_seq` allocation concurrency-safe and label it as capture order.
- Expose observed versus estimated timing in API types.
- Add overlapping-request and capture-boundary tests.

Acceptance: parallel requests can be placed on a real timeline, retain unique stable capture labels,
and are assigned to the capture in which they began.

### Phase 3 - Build the parent-relative context diff engine

- Implement semantic block fingerprints and duplicate occurrences.
- Partition configuration, transcript, current input, and output.
- Implement persisted, promoted, added, removed, and replaced mappings.
- Add category/token summaries and branch-aware introduction fields.
- Expose the edge-detail API.

Acceptance: known parent/child fixtures produce exact occurrence mappings and token totals even after
block content text is purged.

### Phase 4 - Add conservative parent inference

- Build in-memory rare-hash/tool-ID indexes for one capture.
- Implement document-frequency weighting, ordered comparison, scoring, margins, and reason codes.
- Add ambiguous/unavailable states and cycle prevention.
- Calibrate thresholds against real labelled captures.
- Keep inference on read until accuracy and performance are validated.

Acceptance: interleaved branches link correctly, unrelated agents sharing boilerplate do not, and
ambiguous cases remain visibly unresolved.

### Phase 5 - Add the capture lineage API and initial UI

- Add the bulk `/sessions/{id}/lineage` endpoint.
- Add frontend types/hooks and live invalidation.
- Add a lineage tree/table first, with parent/child navigation and edge delta summaries.
- Add previous/children links to request detail.
- Update visible capture terminology without breaking routes or CLI compatibility.

Acceptance: users can navigate exact/inferred branches and inspect what changed without a graphical
canvas.

### Phase 6 - Add the visual graph/timeline

- Add the timeline/lineage switch and branch-lane layout.
- Render duration overlap, edge certainty, forks, unresolved parents, and delta badges.
- Add node and edge inspectors, filters, keyboard access, and large-capture behavior.
- Preserve layout/selection during live capture updates.

Acceptance: the visualization makes parallelism, forks, and context growth understandable while its
accessible tree reports the same information.

### Phase 7 - Add orchestration overlays

- Introduce registered normalized orchestration-hint extractors.
- Store observable conversation, trace, and agent-run identifiers.
- Add exact delegation/contribution edges where metadata proves them.
- Add conservative content-based contribution suggestions.
- Keep these overlays separate from primary context lineage.

Acceptance: a verified subagent capture distinguishes context continuation from delegation and later
result contribution.

### Phase 8 - Persist/cache validated relations and backfill

- Add `request_relations`, indexes, constraints, and algorithm versioning.
- Cache exact/inferred graph edges and delta summaries.
- Implement an idempotent historical backfill with database backup.
- Recompute only stale inferred rows when the algorithm version changes.
- Add diagnostics for exact/inferred/ambiguous/root counts and analysis limits.

Acceptance: large historical captures load predictably, exact evidence is immutable, and inferred
results can be upgraded without stale topology.

### Phase 9 - Reconcile existing session analysis work

- Update `SESSION_ANALYSIS_PLAN.md` so its default request tree follows graph topology.
- Replace capture-global introduction as the default with `introduced_relative_to_parent`.
- Retain the planned block-to-canonical-JSON navigation as a complementary edge/node inspector.
- Update `SPEC.md`, development documentation, CLI help, FAQ, screenshots, and changelog.

Acceptance: specifications no longer imply that consecutive capture requests form one conversation.

## Explicit non-goals for the first release

- Reconstructing hidden provider state that was never observed.
- Declaring two requests causally related from timestamps alone.
- Treating the agent product name as an agent-instance identity.
- Semantic embedding similarity over prompt prose.
- Automatically resolving ambiguous parent candidates.
- Replacing request rows with grouped "turn" rows.
- Renaming database tables or breaking existing session/request API routes.
- Persisting branch labels or assuming a context graph is a simple tree.

## Completion criteria

The complete feature is ready when:

- every invocation in a capture appears exactly once as a graph node;
- exact provider predecessors produce exact request links, including forks;
- requests without exact metadata are linked only by conservative, explainable inference;
- parallel unrelated agents are not serialized into one false chain;
- capture order, real timing, context lineage, and causal overlays are visibly distinct;
- every continuation edge exposes correct persisted/promoted/added/removed/replaced block and token
  summaries;
- branch-relative introduction does not leak across siblings;
- uncertainty, retention, compaction, and missing-predecessor states remain explicit;
- all analysis logic lives in the Python backend;
- graph endpoints avoid N+1 work and support captures larger than 500 requests;
- the UI provides both a visual graph and an accessible tree/table;
- existing request detail URLs, APIs, session data, and historical numbers remain compatible.
