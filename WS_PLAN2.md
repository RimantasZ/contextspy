# Provider Invocation Normalization Plan

## Purpose

ContextSpy should analyze model invocations, not network transports.

A user inspecting a request should see:

- how many provider invocations occurred;
- the effective provider-level context for each invocation;
- system/developer instructions, messages, tool calls, and tool results;
- the generated response, including visible thinking/reasoning data;
- provider-reported input, cached input, output, and reasoning usage.

The normal request list and detail page must behave the same whether the
invocation was observed through JSON HTTP, streamed HTTP, or WebSocket traffic.

## Product definition

One ContextSpy request row represents one externally observable provider
response invocation.

For OpenAI Responses traffic, that normally means one `response.create`
lifecycle with one provider response ID and one terminal usage object. A coding
agent may create several such invocations while processing one user prompt:

```text
user prompt -> model invocation -> tool call
tool result -> model invocation -> tool call
tool result -> model invocation -> final answer
```

This example produces three ContextSpy rows. Streaming events, deltas, timing
messages, pings, and other utility frames do not produce rows.

ContextSpy cannot prove how many private sampling passes a provider performs
inside one response lifecycle. The row count describes the provider
invocations visible through the public protocol, and the terminal usage object
describes the provider's accounting for each row.

## User-facing invariants

1. The request list contains one row per observed provider invocation.
2. The request detail uses one common layout for every transport.
3. Context composition is derived from a complete canonical request document.
4. Output, thinking, and usage are derived from a canonical response document.
5. Provider-reported usage is authoritative for accounting; local tokenization
   explains the visible document.
6. Normal screens do not display frame counts, WebSocket badges, connection
   IDs, or reconstruction mechanics.
7. Incomplete knowledge is reported as partial or opaque context, never filled
   using guesses.

## Scope boundaries

This implementation does not:

- combine multiple genuine invocations into a single agent-turn row;
- create request rows for individual WebSocket frames;
- infer conversation lineage from timestamps, row order, or connection order;
- claim to reproduce provider-internal prompts byte-for-byte;
- fabricate hidden reasoning, compacted history, or purged predecessors;
- require users to switch transports to obtain the common UI.

## Architecture

Use three independent boundaries:

```text
network traffic
    |
    v
Transport ingestion
    - identifies invocation boundaries
    - decodes application payloads/events
    - emits ObservedInvocation
    |
    v
Provider normalization
    - understands provider response/event schema
    - resolves explicit provider-managed state
    - emits CanonicalInvocation
    |
    v
Analysis
    - parses canonical request/response JSON
    - emits blocks, categories, tools, text, thinking, usage
    |
    v
Persistence and transport-neutral API/UI
```

Dependencies flow only downward. Analysis must not import or accept WebSocket
sessions, frames, transport names, or protocol assemblers.

### 1. Transport ingestion

Transport ingestion answers only: "Which decoded application data belongs to
one externally observable invocation?"

HTTP behavior:

- one HTTP request/response pair creates one observed invocation;
- JSON request bodies are retained exactly;
- buffered JSON responses are retained exactly;
- SSE or NDJSON events are collected under that same invocation.

WebSocket behavior:

- a registered protocol assembler consumes decoded client/server messages;
- the assembler opens an invocation only on a provider-defined start event;
- subsequent events are correlated to that invocation;
- a provider-defined terminal event closes it;
- utility events outside an invocation are discarded or retained only as
  optional diagnostics.

Suggested interface:

```python
@dataclass
class ObservedInvocation:
    provider: str
    endpoint: str
    protocol: str
    observed_request_text: str | None
    observed_request: dict[str, Any]
    response_events: tuple[CapturedEvent, ...]
    observed_response_text: str | None
    started_at: float | None
    first_output_at: float | None
    ended_at: float | None
    completion_state: Literal["completed", "failed", "incomplete", "unknown"]
    capture_error: dict[str, Any] | None


class InvocationAssembler(Protocol):
    protocol_id: str

    def accept_client_message(self, message: CapturedEvent) -> list[ObservedInvocation]: ...
    def accept_server_message(self, message: CapturedEvent) -> list[ObservedInvocation]: ...
    def close(self) -> list[ObservedInvocation]: ...
```

The existing HTTP handler acts as a simple assembler. Each WebSocket schema
registers a specialized assembler. Generic proxy code selects an assembler but
contains no Codex/OpenAI event-name conditionals.

### 2. Provider normalization

Provider normalization answers: "What standalone provider-shaped request and
response documents best represent this invocation?"

This is provider-specific but transport-independent. An OpenAI Responses
normalizer can process an invocation observed over REST, SSE, or WebSocket.
This is important because provider-managed state such as
`previous_response_id` may also make a REST request sparse.

Suggested interface:

```python
class ProviderInvocationNormalizer(Protocol):
    provider_protocol: str

    def normalize(
        self,
        observed: ObservedInvocation,
        lineage: InvocationLineageRepository,
    ) -> CanonicalInvocation: ...
```

Adding another stateful provider requires:

- an assembler only if its transport has non-trivial invocation boundaries;
- a normalizer that understands its provider schema and state references;
- its existing or new canonical JSON adapter;
- fixtures and contract tests.

It must not require changes to generic persistence, request APIs, or UI
components.

### 3. Canonical document boundary

The exact JSON analyzed by ContextSpy must also be stored in the database.

```python
@dataclass(frozen=True)
class CanonicalJsonDocument:
    text: str
    value: dict[str, Any]

    @classmethod
    def from_text(cls, text: str) -> "CanonicalJsonDocument": ...

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> "CanonicalJsonDocument": ...


@dataclass(frozen=True)
class CanonicalInvocation:
    request: CanonicalJsonDocument
    response: CanonicalJsonDocument | None
    provider_response_id: str | None
    predecessor_response_id: str | None
    outcome: Literal["completed", "failed", "incomplete", "unknown"]
    context_fidelity: Literal["complete", "partial", "opaque"]
    context_notes: tuple[str, ...]
```

Construction rules:

- For complete REST JSON, parse the observed body without rewriting it. Its
  original text becomes the canonical request text.
- For streamed responses, reduce events into one provider-shaped buffered
  response object and serialize it exactly once.
- For stateful requests, expand the visible provider-managed history into one
  standalone request object and serialize it exactly once.
- The adapter receives `CanonicalJsonDocument.value`.
- Persistence stores `CanonicalJsonDocument.text`.
- JSON is never regenerated from blocks or token-category columns.

For reconstructed WebSocket traffic, "verbatim" means the exact reconstructed
canonical document passed to analysis. The sparse original frame remains a
separate diagnostic artifact.

### 4. Analysis

Expose one entry point:

```python
def analyze_invocation(
    canonical: CanonicalInvocation,
    adapter: WireFormatAdapter,
) -> AnalyzedRequest:
    request_blocks, call_map = adapter.parse_request(canonical.request.value)
    response_blocks, usage = adapter.parse_response(canonical.response.value)
    return AnalyzedRequest(...)
```

The analysis layer must not know:

- whether the invocation used HTTP or WebSocket;
- whether its context was reconstructed;
- how many frames were observed;
- how provider lineage was resolved.

All persisted derived values come from this one result:

- category token totals;
- input and output blocks;
- tool definitions, calls, and results;
- response text and visible thinking;
- local token estimates;
- provider usage and cache fields.

Loading the two canonical database bodies and rerunning this function must be
sufficient to reproduce the derived analysis without transport events.

## Codex/OpenAI Responses normalization

### Invocation boundaries

The Codex WebSocket assembler maps events as follows:

| Event | Handling |
| --- | --- |
| client `response.create` | Open one invocation |
| server `response.created` | Attach the provider response ID |
| output/message/reasoning/tool deltas | Accumulate inside the open invocation |
| output-item completed events | Preserve completed provider output items |
| client `response.inject` | Attach to the active response lifecycle |
| server `response.completed` | Close the invocation with usage |
| server `response.failed` | Close it as failed |
| server `response.incomplete` | Close it as incomplete |
| provider error before completion | Close the relevant failed attempt |
| rate limits, timing, metadata, ping/pong | No request row |

A connection close flushes an open invocation as incomplete. A new
`response.create` cannot silently overwrite an existing open invocation; the
assembler either correlates concurrent IDs correctly or flushes the displaced
attempt as incomplete.

`response.inject` remains part of the active row while it shares the same
response ID and terminal usage. ContextSpy must not invent a second invocation
when the provider exposes only one lifecycle.

### Canonical response reduction

Reduce all invocation-associated server events to the same JSON shape that a
buffered Responses API call would return.

Required behavior:

- preserve provider response ID, model, status, error, and usage;
- assemble text, reasoning summaries, function calls, and custom tool calls;
- preserve item order, item IDs, call IDs, phases, and unknown fields;
- retain completed output items even when the terminal response contains an
  empty `output` array;
- use a non-empty terminal output snapshot as authoritative where the schema
  indicates it is complete;
- never replace accumulated output with an empty terminal placeholder;
- retain partial output for failed and incomplete responses.

### Explicit lineage

Use only provider-issued identifiers:

- current invocation ID from `response.created.response.id`, with a terminal
  response ID as fallback;
- predecessor ID from `previous_response_id`;
- call and item IDs for safe item correlation.

Never resolve lineage from timestamps, session sequence, socket identity, or
the most recently captured row. Multiple children may reference the same
predecessor, and different chains may be interleaved on one connection.

### Root request

If there is no predecessor reference:

1. Remove only the WebSocket command envelope such as
   `type: "response.create"`.
2. Preserve all provider request fields and unknown extensions.
3. Preserve input item order.
4. Treat the resulting provider object as the canonical request.
5. Mark the context complete unless it contains opaque provider state.

### Continuation request

If the exact predecessor is available, construct:

```text
current effective input
    = predecessor canonical input
    + predecessor canonical response output
    + current observed input
```

Because predecessor canonical input is already expanded, this recursively
accumulates the visible conversation:

```text
invocation 1: user message
invocation 2: user message + assistant tool call + tool result
invocation 3: invocation 2 context + assistant tool call + tool result
```

Rules:

- preserve provider item order and original item representations;
- retain developer/system input items that are actual conversation items;
- use current top-level instructions, model, tools, tool choice, reasoning,
  output, cache, and service settings;
- do not copy predecessor top-level configuration merely because it is absent;
- remove `previous_response_id` from the expanded standalone canonical
  request, recording it in lineage metadata instead;
- deduplicate only when a provider-stable item or call ID proves identity;
- never deduplicate equal-looking text.

This produces a request object that can be parsed exactly like an explicitly
expanded REST request.

### Missing predecessor

If `previous_response_id` cannot be found:

- preserve the current observed request items;
- retain the unresolved predecessor ID;
- mark context fidelity `partial`;
- store and display provider usage normally;
- do not borrow content from adjacent rows;
- permit later exact-ID reconciliation if the predecessor arrives out of
  order and retained data is still available.

### Compaction and opaque state

If the provider supplies a compaction/encrypted state item or starts a new root
after compaction:

- preserve the opaque item in canonical JSON;
- do not prepend history from before the reset;
- create an uncategorized/opaque block representing the visible item;
- mark context fidelity `opaque`;
- report the difference between locally visible tokens and provider input
  usage without inventing its contents.

### Adapter coverage

Extend the OpenAI Responses adapter to parse the canonical items observed in
Codex traffic, including:

- `developer`, `system`, `user`, and assistant messages;
- `function_call` and `function_call_output`;
- `custom_tool_call` and `custom_tool_call_output`;
- additional/namespaced tool definitions;
- reasoning and reasoning summaries;
- compaction and compaction triggers;
- output phases and unknown item extensions;
- input tokens and cached/cache-write breakdowns;
- output and reasoning token breakdowns.

These are provider JSON features, so the same adapter behavior applies to REST
and WebSocket canonical documents.

## Persistence

Continue using `requests` as the primary invocation table. Add fields needed
for canonical artifacts and explicit lineage:

```text
canonical_request_body         TEXT NULL
canonical_response_body        TEXT NULL
provider_response_id           TEXT NULL
predecessor_response_id        TEXT NULL
invocation_outcome             TEXT NOT NULL DEFAULT 'unknown'
context_fidelity               TEXT NOT NULL DEFAULT 'complete'
context_notes                  TEXT NULL
```

Add indexes supporting lookup by provider, protocol family, and provider
response ID. Begin with an application-level idempotency check because old
databases may contain duplicates; add a uniqueness constraint only after real
data has been validated.

Body responsibilities:

| Stored value | Purpose |
| --- | --- |
| `canonical_request_body` | Exact provider-shaped request analyzed and displayed |
| `canonical_response_body` | Exact provider-shaped response analyzed and displayed |
| existing `raw_request_body` | Original REST body or sparse WebSocket start frame |
| existing `raw_response_body` | Compatibility field during migration |
| `response_events` | Optional streamed-event diagnostics, not analysis input |
| blocks/category columns | Derived indexes optimized for product queries and UI |

Populate both canonical columns for every new analyzable REST and WebSocket
invocation. Duplicating a REST request in raw and canonical columns is
intentional: it makes the re-analysis contract independent of transport and
legacy field semantics.

Persist the canonical documents and their derived analysis in one transaction.
If analysis fails, retain the canonical documents and capture error so the row
can be debugged and reprocessed later.

Apply the existing sensitive-body retention policy consistently to:

- raw request/response fields;
- canonical request/response fields;
- response event diagnostics;
- retained block contents.

Physical compression or content-addressed storage can be introduced later,
but canonical documents must remain logically lossless and retrievable.

## Usage and context accounting

Store usage for each invocation only from that invocation's canonical terminal
response. Never sum predecessor usage into its successor.

Expose these independent values:

```text
visible_input_tokens     = locally tokenized canonical request composition
provider_input_tokens    = terminal provider usage for this invocation
cached_input_tokens      = provider cached-input usage when reported
cache_write_tokens       = provider cache-write usage when reported
provider_output_tokens   = terminal provider output usage
reasoning_tokens         = provider reasoning usage when reported
unattributed_difference  = provider_input_tokens - visible_input_tokens
visible_coverage         = visible_input_tokens / provider_input_tokens
```

The difference may contain opaque state, provider tokenizer differences,
server transformations, or local parsing gaps. Label it neutrally; do not call
all of it hidden context.

Provider usage is the accounting source. Canonical composition is the
explanation source.

## API

Keep the current request resources and make their primary fields
transport-neutral.

Request detail should resolve:

```text
request_body  = canonical_request_body  ?? raw_request_body
response_body = canonical_response_body ?? raw_response_body
```

Return:

- canonical request and response bodies;
- existing blocks, category totals, tools, response text, and thinking;
- provider usage and cache fields;
- invocation outcome;
- context fidelity and reconciliation values.

Do not require the UI to interpret transport fields to select a body or
analysis path.

If diagnostic data remains useful, expose it through an optional advanced
diagnostics section or focused endpoint. It may contain observed frame text,
event arrays, protocol ID, and capture errors. It must not become a second
request-detail model.

## UI

### Request list

- Render one row per `Request` record.
- Do not show transport badges in the normal table.
- Use a transport-neutral outcome/status.
- Show model, provider, time, latency, context composition, and usage as today.
- Do not group several genuine tool-loop invocations into one row.

### Request detail

- Use canonical request JSON in the request viewer.
- Use canonical response JSON in the response viewer.
- Use the existing block composition and tool inspection UI.
- Preserve visible and withheld-thinking behavior.
- Show provider input, cached input, output, and reasoning usage.
- For complete context, show no reconstruction notice.
- For partial or opaque context, show one concise provider-neutral notice with
  visible tokens, provider input tokens, coverage, and the unattributed
  difference.
- Put transport events behind Advanced diagnostics when retained.

Equivalent REST and WebSocket canonical fixtures must render identically.

## Migration and compatibility

1. Add canonical and lineage columns additively.
2. Preserve existing request IDs, session associations, timestamps, and row
   counts.
3. Treat `raw_response_body` as a compatibility fallback while new writes use
   the explicit canonical response column.
4. Backfill canonical REST bodies from retained raw JSON where safe.
5. Backfill WebSocket continuations only when exact provider IDs and required
   event data exist.
6. Mark unresolved rows partial; never infer predecessors.
7. Rebuild blocks/tool statistics for a backfilled row only from its newly
   stored canonical documents, in one transaction.
8. Do not rewrite unrelated provider rows during a WebSocket backfill.
9. Make migrations and backfills idempotent.

Validate any production-data backfill on a database copy before applying it to
the live database.

## Implementation sequence

### Phase 1: Freeze existing behavior

Add golden tests for currently working providers and transports:

- Anthropic REST/SSE, including thinking and cache usage;
- OpenAI Chat REST/SSE;
- OpenAI Responses REST/SSE;
- Ollama REST/NDJSON;
- blocks, categories, tools, output text, thinking, usage, API shape, and UI
  detail inputs.

No existing non-WebSocket expected output may change during this phase.

### Phase 2: Add canonical document contracts and storage

- Implement `CanonicalJsonDocument` and `CanonicalInvocation`.
- Add canonical request/response columns and lineage/outcome/fidelity metadata.
- Add the transport-neutral analysis entry point.
- Route ordinary REST JSON through an identity canonical-document builder.
- Store the exact canonical text supplied to analysis.

Acceptance: reloading a REST row's canonical bodies and re-analyzing them
reproduces its blocks, categories, tools, output, thinking, and usage.

### Phase 3: Complete Responses event reduction and JSON parsing

- Fix empty terminal output handling.
- Support real Codex custom tools, reasoning, developer messages, compaction,
  output phases, unknown fields, and usage breakdowns.
- Test buffered and streamed representations against the same expected
  canonical response.

Acceptance: known real capture fixtures preserve tool calls, results, output,
thinking, IDs, order, and terminal usage.

### Phase 4: Implement Codex invocation assembly

- Recognize start, correlation, output, injection, terminal, error, close, and
  utility events.
- Emit exactly one `ObservedInvocation` per response lifecycle.
- Keep Codex event names inside the registered assembler.

Acceptance: N `response.create` lifecycles create N invocations, independent of
the number of other frames.

### Phase 5: Implement OpenAI Responses state normalization

- Resolve exact `previous_response_id` lineage from memory with database
  fallback.
- Expand roots, continuations, branches, and interleaved chains.
- Handle missing predecessors and compaction resets.
- Apply the same normalizer to Responses REST and WebSocket invocations.

Acceptance: an explicitly expanded REST fixture and an equivalent stateful
WebSocket fixture produce the same canonical input items and derived context
composition.

### Phase 6: Switch WebSocket persistence to canonical invocations

- Pass normalized WebSocket invocations through the same analyzer and save
  path used by REST.
- Add idempotency by provider response ID.
- Store canonical bodies before/alongside derived analysis.
- Retain raw frames/events only as diagnostics.

Acceptance: every completed supported WebSocket response has one row, one
canonical request, one canonical response, one derived block set, and one
usage record.

### Phase 7: Make API and UI transport-neutral

- Return resolved canonical request/response bodies.
- Remove normal-view transport badges and frame-oriented language.
- Add partial/opaque reconciliation notice.
- Keep diagnostics optional and secondary.

Acceptance: the same canonical invocation renders identically regardless of
source transport.

### Phase 8: Documentation, rollout, and backfill

- Document context fidelity and provider-state limitations.
- Add an idempotent exact-lineage backfill for retained supported captures.
- Measure canonical body storage growth and request-detail response size.
- Validate real Codex tool loops, branches, reconnects, compaction, failures,
  and missing-history cases.
- Roll out behind a WebSocket normalization feature flag if production data
  needs a staged validation period.

## Test matrix

### Invocation counting

- one create plus many deltas plus completion -> one row;
- several create/completion lifecycles -> one row each;
- utility frames only -> zero rows;
- failure before response ID -> one failed attempt;
- connection close with open response -> one incomplete attempt;
- repeated event/provider response ID -> idempotent result;
- injected input under one response ID -> one row.

### Context reconstruction

- complete root request;
- one continuation with a tool result;
- several iterative tool calls;
- two children branching from one response;
- interleaved chains on one socket;
- reconnect resolved through database lineage;
- missing predecessor;
- predecessor arrives after child;
- purged predecessor;
- compaction reset with opaque state;
- current instructions/configuration do not inherit stale predecessor options;
- unknown provider items remain in canonical JSON.

### Response reconstruction

- buffered complete response;
- text deltas;
- reasoning summary/deltas;
- function and custom tool calls;
- output item completed before empty terminal output;
- failed and incomplete terminal responses;
- cached, cache-write, output, and reasoning usage;
- unknown response fields retained.

### Storage and replay

- stored canonical text is exactly the document given to analysis;
- canonical database bodies alone reproduce derived analysis;
- parser failure retains canonical bodies and error diagnostics;
- retention purges canonical/raw/event/block content consistently;
- large histories remain retrievable after any storage optimization.

### Regression

- Claude Code thinking remains present;
- non-WebSocket provider goldens remain unchanged;
- request count and usage totals count each invocation once;
- REST and WebSocket equivalents produce matching blocks/categories/tools;
- direct request-detail routes remain compatible;
- normal UI requires no transport branching.

## Documentation requirements

Document these fidelity cases in the FAQ and developer architecture guide:

1. **Complete:** ContextSpy captured a full request or reconstructed a complete
   visible chain using explicit provider IDs.
2. **Partial:** a referenced predecessor was not captured, was purged, or
   arrived without enough data.
3. **Opaque:** the provider supplied compacted, encrypted, truncated, or other
   state whose original contents are unavailable.

Clarify that:

- these limitations apply to provider-managed REST conversations as well as
  WebSockets;
- provider usage remains authoritative even when composition is incomplete;
- ContextSpy shows provider-level canonical inputs, not undocumented internal
  prompts or private sampling operations;
- cached tokens are still input tokens for the invocation, with billing
  treatment determined by the provider's reported usage and pricing.

## Definition of done

- Each primary row represents one externally observable provider invocation.
- No utility frame becomes a request row.
- Every new analyzable invocation stores verbatim canonical request and
  response documents.
- Analysis can be reproduced from the stored canonical documents alone.
- Complete Codex continuations show accumulated messages, tool calls, and tool
  results in the same composition view as REST.
- Partial and opaque contexts are quantified without invented content.
- Response text, thinking, tools, and provider usage survive WebSocket event
  reduction.
- REST, SSE, NDJSON, and WebSocket paths share the same canonical analysis API.
- Normal APIs and UI do not depend on transport details.
- Adding a new provider schema requires registered ingestion/normalization and
  adapter code, not a redesign of persistence or UI.
- Existing non-WebSocket behavior remains covered by unchanged golden tests.

