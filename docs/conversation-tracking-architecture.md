# Conversation tracking architecture

This is a maintainer companion to the [user guide](request-tracking-and-conversations.md).
The stored facts are requests, provider references, stream-hint digests, and block metadata.
Direct lineage and conversation membership are **derived**, not database columns. A display
group key or `Conversation 1` label can change after new evidence arrives; neither is a durable
identifier for future actions such as moving a request.

## Read pipeline and contracts

```text
Request + BlockRecord rows
    → metadata-only RequestSnapshot / ContextBlock (`db/crud.py`, `lineage_types.py`)
    → direct-parent graph (`analysis/lineage.py`)
    → display-only groups (`analysis/conversation_projection.py`)
    → revisioned graph read service (`db/session_lineage_service.py`)
    → bounded card/context projections (`db/crud.py`)
    → API → presentation-only React components
```

`RequestSnapshot` and `LineageEdge` are the cross-phase Python value types. The graph phase
accepts at most one direct parent per child. A provider predecessor ID has priority; if it
cannot be linked, inference does not guess around it. Without a provider reference, an inferred
parent requires substantive retained/promoted context, a minimum score, and a clear margin over
competing candidates. Adjacency, model, and generic agent labels are never sufficient evidence.
The score is an uncalibrated ranking measure, not a probability. Exact cross-session parents
may appear as external nodes but never add to the current session's request totals.

The projection phase consumes accepted edges and uncertainty states. It may bridge disconnected
segments into one display row on strong stream affinity, but such a bridge **never creates a
direct parent edge**. It can promote a sustained provisional chain from Auxiliary requests or
rejoin it to another row as later evidence arrives. An auxiliary block is a holding area, not
one conversation. Shared ancestors can appear in more than one row; session sequence and totals
must still count each stored request once. The same persisted evidence and analysis version
must produce the same graph and group order.

The read service owns the revision and bounded cache; `db/crud.py` keeps compatibility entry
points and exposes the common graph to Dashboard and Session Detail. The overview projection
fetches only the visible cards and their comparison parents; token/category calculations stay
in Python. `RequestCard`, `RequestFlow`, `ConversationGroupRow`, and `RequestOverview` are
screen-independent presentation components. Dashboard and Session Detail provide query state,
selection, paging, and navigation callbacks. Keep any new scoring, block aggregation, or
conversation-membership rule out of `ui/src/`.

## Revision and cache

The on-demand revision hashes metadata relevant to the session's graph and selected-context
panel: its request rows, matching external provider-predecessor rows, and their block metadata.
It excludes raw/canonical bodies and unrelated sessions. This catches edits to hints, fidelity,
provider IDs, tokens, blocks, deletions, reassignments, and external-parent arrival. It does
not persist the graph or require a schema migration. The revision calculation is proportional
to the session's evidence size, even when the graph cache hits; this is a deliberate correctness
tradeoff to measure against the approximately 2,000-request normal upper bound.

The graph cache keeps at most four sessions, one revision each, and approximately 12 MiB of
serialized graph size. Oversized graphs are recomputed rather than retained. The serialized
size is a proxy, not a strict Python heap bound. The lightweight
`/sessions/{id}/lineage/revision` response lets diagnostics poll without repeatedly sending
the complete graph; a changed revision fetches `/sessions/{id}/lineage`. Other session views
also use the revision to reject stale paging cursors. The full diagnostics endpoint remains
available for compatibility and still returns a complete graph.

API terminology is moving from the older `capture` and `lineage_fragment_count` names to
`session` and `diagnostic_path_count`; both pairs are currently returned as aliases. Do not
repurpose `lineage_fragment_count` to mean a number of conversations. An API version is not
needed for this additive cleanup.

## Extending the analysis

1. Add provider-specific parsing to an adapter and persist any new source evidence, with the
   required additive and data-migration steps if the schema changes. Keep raw provider IDs
   distinct from heuristic stream hints.
2. Add an immutable snapshot field only if the graph truly needs it; add that field to the
   metadata-only read and revision fingerprint together. A new block-level evidence field
   likewise belongs in both the snapshot read and revision hash.
3. Add a focused synthetic positive and negative fixture in `tests/test_lineage.py`. Cover
   candidate competition, forks, opaque/partial context, and cross-session exact links as
   relevant. Check both direct edges and display grouping, because they are different claims.
4. Update the user-facing evidence text and typed API contracts without calculating new
   metrics in React. Verify Dashboard and Session Detail agree on the same revision.
5. Run backend tests, UI tests/build, and bounded large-session/query-count checks. The
   production-readiness plan in `plans/` records the provisional performance envelope and
   further diagnostics-window work if full-graph transfer remains too costly.

No request contents or personal session data should be added to benchmarks or test fixtures.

## Exploratory scale measurement

Run `.venv/bin/python benchmarks/conversation_reads.py --sizes 100 1000 2000` for an
exact predecessor chain, or add `--mode repeated` for a many-candidate repeated-context
stress case. `--no-trace` removes allocation-tracing overhead; the default reports peak
traced allocations. On one local developer run with 2,000 requests and four blocks each,
without tracing, the exact-chain cold/warm dashboard reads took roughly 0.37/0.05 seconds;
the repeated-context case took roughly 4.68/0.05 seconds. The complete diagnostics JSON
was roughly 2.9–3.1 MB. These are single samples, **not** p95 release measurements or a
claim about all block distributions. The provisional cold-analysis target is five seconds
at 2,000 requests; keep the dense case in future profiling, and consider a windowed
diagnostics API if real sessions make full transfer or first usable render too slow.
