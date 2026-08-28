# Transport-Neutral LLM Invocation Normalization Plan

## Status

Replacement plan based on `main` at commit
`7050491abea76da1fe77b432a8437ea23252181f`.

This plan supersedes the earlier logical-request/agent-turn reconstruction
design. None of the following are part of the new implementation:

- grouped agent-turn rows;
- logical-request tables or routes;
- child-invocation selectors;
- context-snapshot block tables;
- predecessor inference by time, adjacency, session, or agent turn.

The implementation starts from the product rule that ContextSpy analyzes LLM
invocations, not transport mechanics.

## Product contract

1. **One primary row means one provider invocation attempt.**
   - One REST request produces one row.
   - One Codex `response.create` exchange produces one row.
   - Failed and incomplete attempts remain visible, just as failed REST
     requests do, but they are not assumed to be billed without reported
     usage.
2. **Frames are never primary rows.**
   Streaming deltas, output-item events, rate-limit notifications, timing
   messages, ping/pong, and other control frames do not create rows.
3. **Transport is not part of the normal user experience.**
   The request list and detail view use the same API, components, composition,
   tools, response text, thinking, and usage fields for REST and WebSocket
   invocations.
4. **Canonical JSON is the analysis source of truth and a retained artifact.**
   Every displayed invocation has a canonical provider request JSON and a
   canonical provider response JSON. Each document is serialized once, stored
   verbatim, and parsed by the existing provider adapter. Blocks, category
   totals, and tool statistics are derived indexes over those stored documents,
   even though this deliberately duplicates data.
5. **Provider usage is authoritative for usage and billing.**
   Local tokenization explains the visible composition. Provider-reported
   input, output, cached, cache-write, and reasoning tokens describe the
   provider's accounting for that invocation.

An important consequence is:

- one `response.create` followed by 129 streaming frames is **one row**;
- 129 distinct `response.create` attempts are **129 rows**, even if they all
  belong to one Codex user turn.

## What exists on `main`

The HTTP/SSE/NDJSON path already establishes the correct analysis contract:

```text
complete request JSON
+ buffered or reconstructed response JSON
        -> WireFormatAdapter
        -> AnalyzedRequest
        -> classify once
        -> Request + blocks + tool_stats
        -> existing request detail
```

Relevant existing seams:

- `contextspy/analysis/capture.py`
  - `CapturedEvent` preserves decoded application events.
  - `CanonicalResponse` holds the buffered provider response reconstructed
    from SSE, NDJSON, or WebSocket events.
- `contextspy/analysis/adapters/base.py`
  - `WireFormatAdapter.parse_request()` converts provider request JSON to
    input blocks.
  - `WireFormatAdapter.parse_response()` converts provider response JSON to
    output blocks and usage.
- `contextspy/analysis/blocks.py`
  - `AnalyzedRequest` is already the transport-neutral representation of one
    invocation.
- `contextspy/proxy/addon.py`
  - `_save_request()` classifies once and persists the request totals, blocks,
    tool statistics, raw/canonical bodies, and provider usage.
- `ui/src/pages/RequestDetail.tsx`
  - The current request detail is the reference UI to preserve.

The Codex WebSocket assembler also already has the correct basic boundaries:

- a client `response.create` opens an exchange;
- response events and deltas are accumulated inside it;
- `response.completed`, `response.failed`, `response.incomplete`, or an error
  closes it;
- an idle utility frame creates no exchange;
- a connection close or a superseding `response.create` flushes an incomplete
  attempt.

Therefore, the current defect is not "one row per frame." The defect is that
`_handle_ws_exchange()` passes the sparse `response.create` body directly to
`parse_request()`. A continuation commonly contains only
`previous_response_id` plus one tool result, while the provider reports tens or
hundreds of thousands of input tokens from the server-managed chain.

`main` reconstructs the streamed **response** JSON, but does not reconstruct
the effective **request** JSON.

## Target architecture

```text
HTTP JSON/SSE/NDJSON
    -> existing request capture / response reconstruction
    -> CanonicalInvocation
                                      \
                                       -> existing WireFormatAdapter
                                      /   -> AnalyzedRequest
WebSocket frames                         -> existing classify + persist
    -> registered WsProtocol                 -> existing Request APIs
    -> ObservedInvocation                    -> existing Request detail
    -> registered InvocationCanonicalizer
    -> CanonicalInvocation
```

The new boundary is `CanonicalInvocation`. Everything after that boundary is
independent of whether the bytes arrived through HTTP or WebSocket.

Treat this as a strict module/API boundary, not just a convention:

```text
transport package
    observe HTTP request or WebSocket exchange
    reconstruct provider state when required
    emit CanonicalInvocation
             |
             v
analysis package
    accepts canonical provider JSON only
    has no WebSocket frame/session/protocol input
             |
             v
persistence and API
    store canonical JSON plus derived blocks and usage
```

The generic analysis function must not accept `transport`, frames, WebSocket
events, `previous_response_id`, or an `ObservedInvocation`. Those values may be
used before the boundary to create the canonical document and retained as
diagnostics/lineage, but they cannot affect parsing after the boundary.

The implementation should initially add this boundary to the WebSocket path
without rewriting the working HTTP/SSE/NDJSON handlers. Once equivalence tests
are green, the HTTP path may use a thin identity builder for the same contract.
This ordering protects existing providers from another broad regression.

## Core contracts

### `ObservedInvocation`

Transport/protocol output before provider context reconstruction:

```python
@dataclass
class ObservedInvocation:
    protocol_id: str
    endpoint: str
    request_payload: dict[str, Any]
    raw_request_text: str | None
    events: list[CapturedEvent]
    started_at: float | None
    first_event_at: float | None
    ended_at: float | None
    complete: bool
    error: dict[str, Any] | None
```

`CompletedExchange` can be renamed to this type, or retained with its docstring
changed from "turn" to "invocation attempt." It must not contain grouping or
context-analysis logic.

### `ContextFidelity`

```python
ContextFidelity = Literal[
    "observed",       # the frame contains a complete standalone input
    "reconstructed",  # a complete visible lineage was expanded
    "partial",        # an explicit predecessor or required item is missing
    "opaque",         # compacted/encrypted provider state cannot be inspected
]
```

This describes how completely ContextSpy can explain the request JSON. It does
not describe the transport and must not reuse `response_reconstructed`, which
already means a streamed response was reduced to canonical response JSON.

### `CanonicalJsonDocument`

Create one small value type that couples the exact stored serialization to the
object supplied to an adapter:

```python
@dataclass(frozen=True)
class CanonicalJsonDocument:
    text: str
    value: dict[str, Any]

    @classmethod
    def from_text(cls, text: str) -> "CanonicalJsonDocument": ...

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "CanonicalJsonDocument": ...
```

- For an ordinary REST JSON request, `text` is the complete decoded request
  body and `value` is parsed from that text without provider-specific changes.
- For a WebSocket invocation, `value` is the reconstructed standalone provider
  request and `text` is generated exactly once from that value.
- Persistence stores `text`; `parse_request()`/`parse_response()` receive
  `value` from the same document.
- No later stage regenerates a different JSON body from parsed blocks.

Here, "verbatim canonical JSON" means the exact canonical document handed to
analysis is retained byte-for-byte. For WebSockets it cannot mean the original
frame text, because the frame is intentionally only one fragment; that
observed frame remains separately available as transport evidence.

### `CanonicalInvocation`

```python
@dataclass
class CanonicalInvocation:
    request: CanonicalJsonDocument
    response: CanonicalJsonDocument | None
    provider_response_id: str | None
    previous_provider_response_id: str | None
    outcome: Literal["completed", "failed", "incomplete", "unknown"]
    context_fidelity: ContextFidelity
    context_notes: tuple[str, ...]
    observed: ObservedInvocation
```

`request.value` is a standalone, provider-shaped representation of the
effective request input. For a resolved Responses continuation, expand the
input and remove transport/lineage fields that would cause the history to be
applied twice if the JSON were replayed. Store lineage in the dedicated
metadata fields instead of adding ContextSpy-only keys to provider JSON.

`response.value` is the existing canonical buffered response shape. Usage is
parsed from it by the existing adapter; usage from predecessors is never
carried into the current invocation.

### `InvocationCanonicalizer`

```python
class InvocationCanonicalizer(ABC):
    canonicalizer_id: str
    protocol_ids: tuple[str, ...]

    def normalize(
        self,
        observed: ObservedInvocation,
        *,
        wire_adapter: WireFormatAdapter,
        lineage: InvocationLineageStore,
    ) -> CanonicalInvocation:
        ...
```

Register canonicalizers by protocol ID. The first implementation is for Codex
Responses. Adding another WebSocket provider should require:

1. a `WsProtocol`/session that identifies invocation boundaries;
2. an `InvocationCanonicalizer` that understands that provider's state model;
3. fixtures and equivalence tests.

It must not require Codex conditionals in the addon, CRUD, API, or UI.

### `InvocationLineageStore`

```python
class InvocationLineageStore(Protocol):
    def get(self, provider: str, response_id: str) -> PersistedCanonicalInvocation | None:
        ...

    def put(self, invocation: PersistedCanonicalInvocation) -> None:
        ...
```

Use an in-memory LRU for active traffic backed by indexed database lookup.
Database fallback is necessary because WebSocket connections are pooled,
reconnected, and interleaved. Lineage is resolved only by explicit provider
IDs; never by "most recent request."

## Codex Responses invocation recognition

The Codex protocol adapter should classify frames as follows:

| Direction/event | Meaning | Row effect |
| --- | --- | --- |
| client `response.create` | invocation attempt begins | open one pending invocation |
| server `response.created` | provider accepted it and assigned a response ID | correlate the pending invocation |
| server output/reasoning/tool deltas | streamed result for the active response | append to that invocation only |
| client `response.inject` | input injected into the active response | append committed input to the same invocation |
| server `response.completed` | terminal response and usage | close as completed |
| server `response.failed` | terminal provider failure | close as failed |
| server `response.incomplete` | terminal partial response | close as incomplete |
| error before a response ID | rejected/failed attempt | close one failed attempt |
| rate limits, timing, metadata, ping/pong, unknown idle frames | diagnostics/control | no row |

Use the provider response ID from `response.created` or the terminal response
to correlate and deduplicate. A `response.create` that never receives an ID may
still be stored as an incomplete/failed attempt, consistent with a failed REST
request, but it is not treated as billed unless usage is present.

`response.inject` deserves an explicit test. It can cause more server work
inside an active response, but it exposes one response ID and one terminal
usage object. ContextSpy cannot truthfully split that into multiple provider
invocations, so it remains one row.

## Canonical request reconstruction for Codex

### 1. Reconstruct the canonical response first

Use `OpenAIResponsesAdapter.reconstruct_response()` on the invocation's
server events. The predecessor's reconstructed `response.output` is required
to expand its successor.

Fix the current reduction rule before relying on it:

- `response.output_item.done` can contain the real reasoning, message, or tool
  item;
- current Codex terminal snapshots can contain `"output": []`;
- an empty terminal list must not erase output already reconstructed from
  output-item events;
- a non-empty authoritative terminal snapshot may replace/complete the
  accumulated output.

### 2. Extract exact lineage

- Current ID: `response.created.response.id`, falling back to the terminal
  response ID.
- Previous ID: `response.create.previous_response_id`.
- Do not use Codex turn IDs, timestamps, connection order, session order, or
  neighboring rows to infer a predecessor.
- Support branches: several invocations may legitimately reference the same
  predecessor.

### 3. Normalize a root invocation

When `previous_response_id` is absent:

- start from the current `response.create` provider fields;
- remove the WebSocket envelope field `type: response.create` from the
  standalone canonical request;
- keep the observed `input` order exactly;
- mark the request `observed` unless it contains opaque compacted state.

### 4. Normalize a resolved continuation

When the exact predecessor exists, build:

```text
effective input
    = predecessor canonical request input
    + predecessor canonical response output
    + current observed input
```

Preserve provider item order, IDs, call IDs, content parts, phases, encrypted
reasoning, and unknown fields. Deduplicate only by provider-stable item IDs or
call IDs where the schema guarantees identity; never deduplicate merely equal
text.

Use current-invocation top-level configuration as authoritative:

- model;
- instructions;
- tools/tool choice;
- reasoning configuration;
- text/output configuration;
- cache, service-tier, storage, and other current request options.

Do not blindly carry top-level configuration from the predecessor. In
particular, official Responses semantics say previous top-level instructions
are not carried by `previous_response_id`. Developer/system **input items** are
conversation items and remain in the expanded input.

If the server's `response.created` snapshot echoes a current option omitted by
the client envelope, a provider-specific allowlist may fill it. Unknown fields
must be preserved, but no option should be invented from an older invocation.

After expansion, remove `previous_response_id` from the standalone canonical
request and store it in the lineage column. Mark the request `reconstructed`.

### 5. Handle compaction as a reset, not ordinary inheritance

Real Codex traffic includes `compaction_trigger` and encrypted `compaction`
items. A post-compaction request can reset `previous_response_id` and send a
new root input containing the opaque compaction item plus visible retained/new
items.

Rules:

- do not prepend the pre-compaction chain to a reset/root request;
- preserve the compaction item in canonical JSON;
- create an opaque `OTHER` input block so the composition shows that some
  provider state exists without inventing its contents;
- mark fidelity `opaque` when encrypted/compacted content cannot be inspected;
- keep provider input usage authoritative and expose the unexplained token
  difference.

### 6. Missing or purged predecessor

If an explicit predecessor cannot be resolved:

- retain the current observed input;
- do not fall back to the latest request;
- do not copy blocks from an adjacent row;
- mark fidelity `partial` with a neutral warning;
- preserve provider usage even though ContextSpy cannot fully categorize it.

Descendants may be reconciled later if their predecessor arrives out of order,
but only through the same explicit response ID.

## Complete the OpenAI Responses adapter

The canonicalizer only works if the existing JSON-to-block adapter understands
the provider items actually present in Codex captures. Extend and test:

- request roles: `developer` as instruction/system content;
- input items:
  - `message` and `agent_message`;
  - `function_call` / `function_call_output`;
  - `custom_tool_call` / `custom_tool_call_output`;
  - `additional_tools` and nested tool namespaces;
  - `reasoning`;
  - `compaction` / `compaction_trigger`;
- response items:
  - messages and reasoning;
  - function calls;
  - custom tool calls;
  - compaction;
- streamed events:
  - `response.custom_tool_call_input.delta`;
  - `response.custom_tool_call_input.done`;
  - output-item snapshots whose content is absent from the terminal snapshot;
- usage:
  - `input_tokens`;
  - `input_tokens_details.cached_tokens`;
  - `input_tokens_details.cache_write_tokens` when present;
  - `output_tokens`;
  - `output_tokens_details.reasoning_tokens`;
  - unknown usage fields retained in `Usage.extra`.

All of these changes belong to the OpenAI Responses adapter and apply equally
to REST and WebSocket canonical JSON. They must not be implemented as UI or
database special cases.

Preserve the existing shared `reconcile_thinking()` behavior. Visible/derived
`tokens_output_thinking` and optional provider-reported reasoning tokens remain
separate signals.

## One analysis and persistence path

Add a small `analyze_canonical_invocation()` function that does only this:

```text
CanonicalInvocation.request.value -> adapter.parse_request()
CanonicalInvocation.response.value -> adapter.parse_response()
                         -> one AnalyzedRequest
                         -> existing _save_request()
```

Its public signature should be equivalent to:

```python
def analyze_canonical_invocation(
    invocation: CanonicalInvocation,
    adapter: WireFormatAdapter,
) -> AnalyzedRequest:
    ...
```

It must be usable by loading only `canonical_request_body` and
`canonical_response_body` from the database. This makes a saved invocation
replayable for troubleshooting, adapter upgrades, and future re-analysis
without replaying transport frames.

The following must all derive from that same `AnalyzedRequest`:

- token category columns;
- `blocks`;
- `tool_stats`;
- response text;
- response thinking;
- estimated input/output totals;
- provider usage fields.

Do not build a parallel context snapshot, independently summarize inherited
blocks, or replace the normal block set after persistence. The persisted
canonical JSON and persisted blocks must always agree.

Keep capture failures independent from analysis failures: a canonical body and
diagnostic warning should still be saved when one block parser fails.

## Persistence design

Keep `requests` as the only primary invocation table. Add only fields required
to store canonical request state and correlate lineage:

```text
canonical_request_body        TEXT NULL
canonical_response_body       TEXT NULL
provider_response_id          TEXT NULL
previous_provider_response_id TEXT NULL
invocation_outcome            TEXT NOT NULL DEFAULT 'unknown'
context_status                TEXT NOT NULL DEFAULT 'observed'
context_warning               TEXT NULL
```

Add indexes for provider response ID and previous response ID. Start with
non-unique indexes plus an idempotent application-level lookup because existing
databases may contain duplicate or partially captured history. A uniqueness
constraint can be added later after real-data validation.

Body semantics:

- `canonical_request_body` is the exact JSON document displayed and analyzed.
- `canonical_response_body` is the exact canonical provider response document
  displayed and analyzed, whether the provider returned buffered JSON or it
  was reduced from SSE/NDJSON/WebSocket events.
- `raw_request_body` remains the observed HTTP body or WebSocket trigger frame
  for diagnostics and possible reprocessing.
- `raw_response_body` remains a compatibility alias during migration. New code
  reads `canonical_response_body` first; it must not treat streamed wire bytes
  as canonical JSON.
- `response_events` remains optional diagnostic evidence; it is not a second
  analysis source.

Populate both canonical columns for every new analyzable REST or WebSocket row.
For REST JSON, `canonical_request_body` will intentionally duplicate
`raw_request_body`; this is preferred because it keeps the downstream contract
and re-analysis workflow uniform. Existing historical rows may use a temporary
API/read fallback while they are migrated or until their retained raw body is
purged.

This duplication is intentional:

- canonical bodies are the durable, provider-shaped debugging and re-analysis
  artifacts;
- blocks and token/category columns are optimized derived indexes for the UI;
- raw request text and event logs are transport evidence.

Never reconstruct canonical JSON from blocks. If a parser bug is found, rerun
the adapter against the stored canonical bodies and replace only the derived
analysis transactionally.

Full frame storage is not required for the feature. Invocation-associated
events may stay in `response_events` under existing retention. Idle utility
frames should be discarded. If full transport diagnostics materially inflate
the database, place them behind an opt-in capture setting rather than exposing
them in the normal product.

Update raw-body retention so both canonical bodies and any future transport
diagnostics are purged with the existing raw payload fields.

### Context and billing reconciliation

Do not manufacture blocks for unobservable server-managed tokens. Compute
neutral metadata from existing per-invocation fields:

```text
visible analyzed input = tokens_total_input
provider input         = provider_input_tokens
signed difference      = provider input - visible analyzed input
coverage               = visible analyzed input / provider input
cached share           = cache_read_tokens / provider input
```

The signed difference includes opaque state, provider tokenizer differences,
and protocol/framing effects. Label it "unattributed / tokenizer difference,"
not definitively "hidden server data."

Provider input/output/cache/reasoning usage is never reconstructed by summing
predecessors. Each row stores only the terminal usage for that invocation.

### Outcome instead of fake HTTP status

Successful WebSocket invocations currently have `status_code = NULL`, while
some filters interpret null as an error. Do not synthesize HTTP 200. Populate a
transport-neutral `invocation_outcome`:

- HTTP 2xx or `response.completed` -> `completed`;
- HTTP/provider failure or `response.failed` -> `failed`;
- connection close, superseded exchange, or `response.incomplete` ->
  `incomplete`;
- otherwise -> `unknown`.

Retain the real HTTP status code where one exists. Filters and status badges
should prefer `invocation_outcome`.

## API contract

Preserve the existing resources and meanings:

- `GET /requests`
- `GET /requests/{id}`
- `GET /requests/{id}/blocks`
- stats and tool endpoints
- live `new_request` events

Add a resolved `request_body` field to request detail:

```text
request_body = canonical_request_body ?? raw_request_body
response_body = canonical_response_body ?? raw_response_body
```

Keep legacy/raw fields for compatibility and diagnostics. Add context status,
outcome, response lineage IDs, and computed context reconciliation metadata.

Do not add `/logical-requests`, `/request-entries`, `/agent-turns`, or a second
detail resource.

Stats remain invocation-based:

- request count is the number of persisted invocation attempts;
- completed/failed/incomplete counts come from `invocation_outcome`;
- analyzed composition sums the existing category fields;
- provider-usage totals sum each row's provider-reported usage once;
- no agent-turn count is introduced.

## UI contract

### Request list

- Continue rendering one ordinary `RequestTable` row per `Request`.
- Remove the `WS` badge.
- Use the same outcome badge for REST and WebSocket rows.
- Keep the same token, composition, provider, model, agent, latency, and
  session columns.
- Do not group or collapse genuine invocations.

### Request detail

- Keep the existing `/requests/:id` route and `RequestDetail` page.
- Feed the request viewer `request_body`, not the sparse wire payload, and the
  response viewer `response_body`.
- Feed composition, block inspection, tool charts, response text, thinking,
  and output split from the existing row/blocks APIs.
- Remove the primary Transport field and "Reconstructed from WebSocket" badge.
- Do not show successful transport reconstruction as a special state.
- Show a small transport-neutral warning only for `partial` or `opaque`
  context, with provider input, visible analyzed input, coverage, cache share,
  and unattributed/tokenizer difference.
- Keep raw frames/events, if retained, behind an optional Advanced diagnostics
  section rather than a default analysis tab.

Equivalent canonical REST and WebSocket invocations should be visually
indistinguishable in the main list and detail views.

## Schema and data migration

### Structural migration

Add the new columns and indexes additively. The current released main schema is
version 2, but a user's database may already contain version-4 tables/columns
from the abandoned branch. Use a new migration version at or above 5 and never
reuse the abandoned version numbers.

First fix migration bookkeeping so that:

- migrations newer than a stored version are discovered even when the pending
  list is empty;
- a database with an unknown newer version is not silently downgraded;
- abandoned `logical_requests`, `context_snapshot_blocks`, and similarly named
  columns are ignored by the new runtime;
- no destructive cleanup of those abandoned tables occurs automatically.

Use fresh canonical field names rather than trusting derived values written by
the abandoned implementation.

### Optional WebSocket backfill

Implement an explicit, idempotent data migration for supported WebSocket rows
whose raw trigger frames and response events still exist:

1. select only registered WebSocket protocol rows;
2. extract provider IDs from captured response events/canonical responses;
3. construct a response-ID graph;
4. process roots before descendants, supporting forks and out-of-order rows;
5. reconstruct canonical request JSON only through exact lineage;
6. create one `AnalyzedRequest` from canonical request/response JSON;
7. in one row transaction, replace that row's category fields, blocks, and
   tool statistics, then store canonical/status metadata;
8. leave the existing `Request` ID, timestamp, session, and row count unchanged.

If a row cannot be safely reconstructed because data was purged or its
predecessor is missing, mark it partial or leave its old analysis untouched.
Never infer context. Never touch HTTP/SSE/NDJSON rows.

Validate the migration on a copy of the real database before offering it for
the live database.

## Implementation phases

### Phase 0 - Freeze non-WebSocket behavior

Add golden tests for current `main` before production changes:

- Anthropic JSON and SSE, including visible/hidden thinking and cache usage;
- OpenAI Chat JSON and SSE;
- OpenAI Responses REST and SSE;
- Ollama JSON and NDJSON;
- request API payloads, block order/types, category totals, tool statistics,
  output text/thinking, and provider usage;
- existing request list and detail behavior.

Acceptance: all goldens pass without changed non-WebSocket expected values.

### Phase 1 - Strengthen invocation boundary fixtures

- Rename/document `CompletedExchange` as an invocation attempt.
- Add sanitized fixtures from real Codex traffic.
- Test create/created/terminal correlation and provider IDs.
- Test utility frames, `response.inject`, errors, close, supersede, and
  duplicate events.

Acceptance: N `response.create` attempts produce N exchanges; any number of
non-create frames produces no additional exchange.

### Phase 2 - Fix canonical Responses response reconstruction

- Preserve accumulated output when terminal `output` is empty.
- Add custom tool-call stream events and item types.
- Preserve reasoning, messages, compaction, unknown fields, IDs, and ordering.
- Parse cached/cache-write/reasoning usage.

Acceptance: canonical response JSON, output blocks, thinking, tool calls, and
usage match real terminal/event fixtures.

### Phase 3 - Introduce canonical invocation contracts

- Add `ObservedInvocation`, `CanonicalJsonDocument`, `CanonicalInvocation`,
  `ContextFidelity`, and the canonicalizer registry.
- Add the explicit-ID lineage store with memory and database implementations.
- Keep generic addon/persistence code free of Codex field names.
- Make the analysis entry point consume only `CanonicalInvocation`; do not give
  it access to transport/session objects.

Acceptance: a fake second WebSocket provider can register an identity
canonicalizer without editing generic code.

### Phase 4 - Implement Codex canonical request reconstruction

- Implement roots, continuations, branches, current-config merge rules,
  missing predecessors, compaction reset, and injected input.
- Extend OpenAI Responses request parsing for real Codex item types.
- Produce a standalone full canonical request JSON before any block analysis.

Acceptance: a REST Responses request with explicit full history and an
equivalent Codex WebSocket continuation yield equivalent input-block
signatures, categories, tool attribution, and visible token totals.

### Phase 5 - Persist one normalized invocation

- Add minimal columns/indexes and migration bookkeeping fixes.
- Populate verbatim canonical request and response bodies for every new REST
  and WebSocket invocation; keep observed wire payloads separately.
- Route only the WebSocket handler through canonicalization initially.
- Analyze and persist once through the existing `AnalyzedRequest` and
  `_save_request()` logic.
- Add idempotent response-ID duplicate protection.

Acceptance: each accepted/completed provider response ID has one Request row,
one canonical request, one canonical response, one block set, and one usage
record. Reloading its canonical bodies and rerunning the adapter reproduces the
stored block/category/tool analysis.

### Phase 6 - Make API and UI transport-neutral

- Add resolved `request_body`, outcome, context quality, and reconciliation
  metadata.
- Remove primary transport badges/fields and grouping concepts.
- Reuse the current request detail components unchanged wherever possible.
- Put raw events behind Advanced diagnostics, if retained.

Acceptance: equivalent REST and WebSocket invocations render the same analysis
without transport-specific navigation or labels.

### Phase 7 - Backfill and real-data validation

- Implement the WebSocket-only, exact-lineage backfill.
- Run on a database copy.
- Compare row counts, unique provider response IDs, provider usage totals,
  reconstructed composition, tool links, thinking, partial/opaque counts, and
  database size before and after.
- Exercise the UI in a browser with a root call, several tool continuations, a
  compaction reset, a missing predecessor, and a failed call.

Acceptance: no non-WebSocket row changes, no row-count inflation, no missing
thinking/output, and no fragmented continuation composition when lineage is
available.

## Test matrix

### Invocation boundaries

- one create + many deltas + completed -> one row;
- two creates with distinct response IDs -> two rows;
- utility frames without create -> zero rows;
- error before created -> one failed attempt without assumed usage;
- failed/incomplete with and without usage;
- close/supersede flush;
- `response.inject` remains in the active row;
- duplicate sequence/event/response ID is idempotent.

### Request reconstruction

- full root snapshot;
- one and many continuations;
- two children branching from one predecessor;
- interleaved response chains on pooled connections;
- reconnect with predecessor resolved from the database;
- missing and retention-purged predecessor;
- out-of-order predecessor arrival and later reconciliation;
- compaction trigger followed by opaque reset root;
- current instructions/tools replace rather than inherit old configuration;
- unknown provider fields preserved.

### Provider content and usage

- developer/system/user/assistant messages;
- custom and function tool calls/results with ID/name links;
- nested/additional tools;
- reasoning summary and hidden reasoning;
- compaction items;
- empty terminal output does not erase streamed output;
- provider input/output/reasoning/cached/cache-write usage;
- provider token total smaller or larger than local estimate.

### Regression and equivalence

- REST/SSE/NDJSON goldens unchanged;
- Claude Code thinking remains visible/derived as before;
- REST and WebSocket canonical equivalents produce the same blocks and UI;
- `tokens_*`, blocks, donut, tool stats, and canonical JSON agree;
- stats count invocations and usage exactly once;
- API routes and direct `/requests/:id` links remain compatible.
- persisted canonical JSON is byte-for-byte the document given to analysis;
- re-analysis from canonical database bodies requires no raw transport events;
- generic analysis modules do not import WebSocket protocol/session modules.

## Risks and mitigations

### Server state cannot always be made visible

Encrypted reasoning, compaction, truncation, and provider-side transformations
can prevent exact reconstruction. Preserve what was observed, use explicit
`partial`/`opaque` status, and reconcile against provider input usage. Never
invent content to force 100% coverage.

### Canonical JSON can be large

Expanded history repeats across many invocations. Existing content-addressed
blocks and retention reduce long-term cost, while dropping default full-frame
diagnostics avoids a second large copy. Measure the real database in Phase 7;
payload compression/content addressing can be a later storage optimization
without changing the canonicalization contract. Canonical JSON must remain
logically lossless and retrievable even if its physical storage is optimized.

### Observable-context limits must be documented

Add a short section to the product FAQ and development documentation covering
the three fidelity cases:

- complete observed or explicitly reconstructed lineage;
- partial lineage because a predecessor was not captured or was purged;
- opaque provider state caused by compaction, encryption, truncation, or other
  server-side transformations.

Explain that provider-reported usage is authoritative for the invocation while
local composition describes the visible canonical document. Do not frame these
as WebSocket limitations: REST APIs using provider-managed conversation state
have the same observability boundary.

### Concurrent chains can leak context

Codex can run parallel agents and pooled connections. Resolve only explicit
provider response IDs and test interleaving and forks. Never use connection or
timestamp proximity as semantic lineage.

### Provider schemas evolve

Preserve unknown fields in canonical JSON and diagnostics, keep boundary and
canonicalization logic registered per protocol, and fail to `partial` rather
than silently dropping an invocation.

### A compatibility refactor can regress REST providers

Land non-WebSocket goldens first, introduce normalization only in the WebSocket
path, and move shared code only after semantic equivalence is demonstrated.

## Definition of done

- Every primary row represents one discerned provider invocation attempt.
- No utility WebSocket frame creates a primary row or affects request stats.
- Every supported WebSocket row stores and displays a canonical request JSON
  and canonical response JSON.
- Every new REST row stores the same canonical request/response artifacts, even
  when that duplicates the observed request body.
- Resolved continuations show their full visible context composition, tools,
  tool results, conversation history, output, and thinking in the existing
  request detail.
- Canonical JSON, blocks, category totals, and tool stats come from one
  `AnalyzedRequest` and cannot disagree by construction.
- Provider response IDs, outcomes, input/output/reasoning/cache usage, and
  context reconciliation are stored per invocation.
- Partial or opaque provider state is clearly quantified without being
  misrepresented as observed content.
- REST and WebSocket invocations use the same list, detail route, and analysis
  components with no primary transport badges or grouping concepts.
- Existing Anthropic, OpenAI Chat, OpenAI Responses, Ollama, SSE, and NDJSON
  behavior passes golden regression tests.
- Adding another WebSocket provider requires a protocol assembler,
  canonicalizer, and tests, not changes to CRUD/API/UI.
- Saved canonical bodies alone are sufficient to rerun provider parsing and
  reproduce derived analysis; transport events are optional diagnostics.
- The migration is idempotent, supports databases touched by the abandoned
  branch, and never rewrites non-WebSocket rows.

## Reference semantics

The lineage and cache rules should be kept in sync with the official OpenAI
Responses documentation. In particular, current model guidance describes
using `previous_response_id` for prior state, preserving every response output
item when managing state manually, persisted reasoning context, and reporting
cached/cache-write tokens:

- <https://developers.openai.com/api/docs/guides/latest-model>
