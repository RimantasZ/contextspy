# WebSocket Grouping and Context Reconstruction Plan

## Goal

Make Codex WebSocket traffic appear as one logical request per user turn while retaining every underlying model invocation for accurate debugging, context analysis, and token accounting.

The implementation should also provide stable extension points for providers with different WebSocket schemas and conversation-continuation rules.

## Problem Summary

The current WebSocket implementation correctly treats every Codex `response.create` exchange as a separate request/response pair. Each exchange is a real model invocation, but one Codex user turn can generate several such invocations while the agent runs tools and returns their results to the model.

At present, every completed exchange is immediately persisted as a top-level `Request`. The UI therefore presents one logical Codex turn as several unrelated rows.

Continuation calls can also look artificially small. A request may contain only a `previous_response_id` and one tool result, while OpenAI restores earlier conversation state on the server. The small WebSocket frame is therefore not the complete context used by the model.

The latest capture changes improve WebSocket event preservation and response reconstruction, but they do not yet provide:

- Logical-turn grouping.
- Request lineage through `previous_response_id`.
- Effective context reconstruction.
- A clear distinction between observed, reconstructed, and provider-reported tokens.
- Codex `custom_tool_call` parsing.

## Product Model

```text
ContextSpy session
  └── Logical request (one user/Codex turn)
       ├── Model invocation 1
       ├── Model invocation 2 after a tool result
       ├── Model invocation 3 after another tool result
       └── Final model invocation
```

Every `response.create` remains a physical model invocation in storage. The request list shows a logical request by default, with its physical invocations available as an expandable timeline.

A normal REST call becomes a logical request containing one physical invocation, so REST and WebSocket traffic share the same product model.

## Design Principles

1. Preserve every physical model invocation.
2. Do not equate a WebSocket frame with the model's complete context.
3. Treat provider-reported usage as authoritative for tokens processed by the provider.
4. Label reconstructed context as best-effort when provider-managed content is unavailable.
5. Keep transport framing separate from provider conversation semantics.
6. Implement grouping and reconstruction through provider adapters rather than Codex conditions in shared code.
7. Do not group by WebSocket connection ID because connections may be pooled and reused.

## Proposed Architecture

```text
HTTP / SSE / WebSocket
        ↓
Transport decoder or WebSocket protocol
        ↓
Physical model invocation
        ↓
Wire-format adapter
        ├── canonical response
        ├── input/output blocks
        └── provider usage
        ↓
Conversation adapter
        ├── invocation identity
        ├── continuation relationship
        ├── logical grouping identity
        └── context mutation
        ↓
Provider-neutral lineage and context engine
        ↓
Persistence, API, and UI
```

### Transport Layer

Keep `WsProtocol`, `WsSession`, `CompletedExchange`, and `CapturedEvent` focused on transport responsibilities:

- Match a WebSocket endpoint.
- Identify request and response boundaries.
- Preserve application frames in order.
- Emit one completed physical exchange.
- Mark failed or incomplete exchanges.
- Retain unknown text, JSON, and binary frames.

The transport layer must not decide which exchanges belong to one user turn or how previous provider state affects model context.

Relevant files:

- `contextspy/proxy/ws_protocols/base.py`
- `contextspy/proxy/ws_protocols/codex.py`
- `contextspy/analysis/capture.py`

### Wire-Format Adapter

Continue using `WireFormatAdapter` for provider payload parsing:

- Request JSON to input blocks.
- Response JSON to output blocks and usage.
- SSE or WebSocket events to a canonical buffered response.

This layer understands the provider's payload schema but does not maintain conversation lineage.

### Conversation Adapter

Add a separate provider extension point for continuation and grouping semantics:

```python
class ConversationAdapter(ABC):
    def identify(self, capture: InvocationCapture) -> InvocationIdentity:
        ...

    def context_mutation(self, capture: InvocationCapture) -> ContextMutation:
        ...

    def logical_request_key(
        self, identity: InvocationIdentity
    ) -> LogicalRequestKey | None:
        ...
```

Suggested normalized types:

```python
@dataclass
class InvocationIdentity:
    provider_request_id: str | None
    previous_provider_request_id: str | None
    provider_conversation_id: str | None
    logical_turn_id: str | None
    agent_id: str | None
    parent_turn_id: str | None
    confidence: str


@dataclass
class LogicalRequestKey:
    provider: str
    conversation_id: str
    turn_id: str
    agent_id: str | None


@dataclass
class ContextMutation:
    operations: list[ContextOperation]
    completeness: str
    warnings: list[str]
```

Generic context operations should include:

- `append(items)`
- `replace_instructions(items)`
- `remove(category_or_ids)`
- `reset()`
- `compact(opaque_item)`
- `unknown(reason)`

Provider adapters describe mutations; the shared reconstruction engine applies them. This prevents future provider-specific continuation rules from leaking into the core pipeline.

### Provider Integration Registry

Bundle compatible components in a provider integration descriptor:

```python
ProviderIntegration(
    integration_id="codex_responses",
    wire_adapter=OpenAIResponsesAdapter(),
    conversation_adapter=CodexConversationAdapter(),
    websocket_protocol=CodexResponsesProtocol(),
)
```

Matching may use provider, host, endpoint, transport, and request characteristics. Endpoint-only wire-format matching can remain available for gateways that expose another provider's schema.

A new provider should require only:

- A WebSocket protocol if its framing is new.
- A wire-format adapter if its message schema is new.
- A conversation adapter if its continuation or grouping semantics are new.
- Registration and provider-specific fixtures.

It should not require changes to the addon, context engine, database writer, API shape, or UI grouping logic.

## Implementation Phases

### Phase 1: Correct Codex Tool Parsing

Fix the immediate analysis gaps before implementing lineage.

Update `contextspy/analysis/adapters/openai_responses.py` to:

- Parse request items with type `custom_tool_call_output`.
- Flatten list-based `input_text` output into a tool-result block.
- Parse response items with type `custom_tool_call`.
- Reconstruct `response.custom_tool_call_input.delta` events.
- Reconstruct `response.custom_tool_call_input.done` events.
- Preserve tool name, call ID, item ID, arguments, and result linkage.
- Retain unknown custom item fields in block attributes or canonical events.

Add fixtures based on real Codex frames rather than only standard Responses API function calls.

Expected result: physical Codex invocations show their actual observed tool calls and tool results instead of appearing nearly empty.

### Phase 2: Add Logical Identity and Lineage

Create a `logical_requests` table with fields similar to:

- `id`
- `session_id`
- `provider`
- `provider_conversation_id`
- `logical_turn_id`
- `agent_id`
- `parent_logical_request_id`
- `started_at`
- `completed_at`
- `state`
- `grouping_confidence`
- `grouping_metadata`

Add these fields to physical `requests`:

- `logical_request_id`
- `provider_request_id`
- `previous_provider_request_id`
- `provider_conversation_id`
- `logical_turn_id`
- `invocation_seq`
- `lineage_status`
- `identity_metadata`

Add indexes for:

- `(provider, provider_request_id)`
- `(provider, previous_provider_request_id)`
- `logical_request_id`
- `(provider, provider_conversation_id, logical_turn_id)`

For Codex, extract identity from the response ID, `previous_response_id`, and observed `client_metadata`, including fields such as:

- `thread_id`
- `turn_id`
- `root_turn_id`
- `agent_name`

The preferred Codex grouping key should be based on provider, thread, root turn, and agent. Subagent calls should be separate logical requests when they have a distinct agent identity, with an optional parent relationship to the initiating logical request.

If authoritative metadata is unavailable, fall back to response lineage and session timing, but mark the grouping confidence as inferred.

### Phase 3: Build the Context Reconstruction Engine

Add a provider-neutral `ContextReconstructor` service.

For each physical invocation:

1. Resolve its predecessor using `previous_provider_request_id`.
2. Load the predecessor's context snapshot.
3. Ask the conversation adapter for the current context mutation.
4. Apply provider-specific continuation rules through generic operations.
5. Add eligible predecessor output items and current input items.
6. Apply instruction replacement, reset, truncation, or compaction operations.
7. Categorize and tokenize the reconstructed visible context.
8. Reconcile it with provider-reported input usage.
9. Persist the snapshot and reconstruction status.

Do not reconstruct context by blindly concatenating every earlier raw request. Provider continuation rules can exclude, replace, compact, or hide content. For example, OpenAI documents that previous `instructions` are not automatically carried into a new response when `previous_response_id` is used.

Support lineage states:

- `root`
- `resolved`
- `unresolved_predecessor`
- `forked`
- `compacted`
- `incomplete_capture`
- `unsupported`

When a predecessor arrives later or a historical row is backfilled, retry unresolved descendants.

### Phase 4: Persist Context Snapshots Efficiently

Keep the current `blocks` table as the blocks directly observed for a physical request. Do not silently change its meaning.

Add a separate `context_snapshot_blocks` table for reconstructed effective context:

- `request_id`
- `position`
- `source_request_id`
- `source_block_id`
- `content_hash`
- `direction`
- `block_type`
- `category`
- `token_count`
- `provenance`
- `attrs`

Use the existing content-addressed `block_contents` table so repeated prompts, tool definitions, and conversation history are referenced rather than duplicated.

Suggested provenance values:

- `observed_current`
- `inherited_input`
- `inherited_output`
- `server_managed`
- `compacted`
- `synthetic_residual`

Add reconstruction summary fields to `requests`:

- `observed_input_tokens`
- `reconstructed_input_tokens`
- `unattributed_input_tokens`
- `input_token_variance`
- `context_coverage_pct`
- `context_reconstruction_status`
- `cache_write_tokens`

The existing `tokens_total_input` can remain the locally estimated observed value for backward compatibility, with clearer names exposed through the API.

### Phase 5: Token Accounting

For each physical invocation, report three distinct measurements:

1. **Observed input**: content present in the captured request frame.
2. **Reconstructed visible context**: observed and inherited content ContextSpy can recover.
3. **Provider-reported input**: the provider's authoritative input-token usage for that invocation.

Calculate the unexplained provider-managed portion as:

```text
unattributed input = max(provider input - reconstructed visible input, 0)
```

Unattributed input may include:

- Server-managed instructions.
- Hidden or encrypted state.
- Compacted context.
- ContextSpy tokenizer differences.
- Provider formatting that was not observable on the wire.

This value explains the provider-reported total. It must not be added on top of that total.

If reconstructed tokens exceed provider-reported input, store and display the signed variance instead of forcing the figures to match.

Parse and persist provider-reported:

- Input tokens.
- Cached input tokens.
- Cache-write tokens when available.
- Output tokens.
- Reasoning tokens.
- Other provider-specific usage in `usage_extra`.

For a logical request, calculate:

- `model_call_count`
- `peak_context_tokens = max(provider_input_tokens)`
- `final_context_tokens = last provider_input_tokens`
- `cumulative_input_tokens = sum(provider_input_tokens)`
- `cumulative_cached_tokens`
- `cumulative_cache_write_tokens`
- `cumulative_output_tokens`
- `cumulative_reasoning_tokens`
- `first_ttft_ms`
- `total_duration_ms`

Do not label cumulative input as the context window. Peak or final input describes context size; cumulative input describes total model processing and is the more relevant usage/billing measure.

### Phase 6: Integrate the Processing Pipeline

Refactor the save flow into explicit services:

```text
CompletedExchange
  → reconstruct canonical response
  → parse request, response, and usage
  → extract provider identity
  → persist physical invocation
  → link predecessor
  → resolve/create logical request
  → build context snapshot
  → update logical aggregates
  → broadcast UI update
```

The addon should orchestrate these services rather than contain Codex-specific logic.

Persist the physical invocation even when identity extraction, lineage resolution, or context reconstruction fails. Record each failure independently so partial capture remains useful.

### Phase 7: API Changes

Keep the existing physical-request endpoints for compatibility:

- `GET /requests`
- `GET /requests/{request_id}`
- `GET /requests/{request_id}/blocks`

Add logical-request and context endpoints:

- `GET /logical-requests`
- `GET /logical-requests/{logical_request_id}`
- `GET /logical-requests/{logical_request_id}/invocations`
- `GET /requests/{request_id}/context`

The logical request response should include its aggregates and a compact invocation summary. Full raw bodies and event lists should remain on the physical request endpoint.

Optionally add `view=logical|invocations` to request-list queries after the new API has stabilized.

### Phase 8: UI Changes

Change the request table to show logical requests by default.

Each logical row should display:

- Provider and agent.
- Model or models used.
- Number of model calls.
- Peak context.
- Cumulative input processed.
- Total output and reasoning.
- Cache-hit percentage.
- Completion or error state.
- Grouping/reconstruction warning when confidence is low.

The logical request detail should contain an expandable invocation timeline. Each child invocation should show:

- Invocation sequence.
- Provider response and predecessor IDs.
- Observed input.
- Reconstructed context.
- Provider-reported input.
- Cached, cache-write, and unattributed tokens.
- Output and reasoning tokens.
- Tool call or tool-result reason for the continuation.
- Raw request, canonical response, and captured events.

Represent unattributed input as a clearly labeled segment in the context composition rather than assigning it to a guessed semantic category.

Broadcast a `logical_request_updated` event whenever an invocation is added to an existing logical request. The UI should update the existing row instead of adding another top-level row.

### Phase 9: Migration and Backfill

Create an explicit data migration that:

1. Scans retained WebSocket request and response bodies.
2. Extracts provider response IDs and `previous_response_id` values.
3. Extracts Codex client metadata.
4. Links physical invocation chains.
5. Creates logical request groups.
6. Rebuilds context snapshots in lineage order.
7. Recalculates token reconciliation fields.

Rows whose raw bodies and events have been purged should remain standalone and be marked as legacy or unknown-confidence rather than guessed into groups.

Include two adjacent correctness fixes:

- Backfill historical `response_complete` correctly instead of treating every row created before the column existed as incomplete.
- Do not classify a successful WebSocket invocation as an error merely because it has no HTTP status code.

## Testing Strategy

### Unit Tests

- Codex `custom_tool_call` response parsing.
- Codex `custom_tool_call_output` request parsing.
- Custom tool delta/done reconstruction.
- Conversation identity extraction.
- Logical grouping-key generation.
- Generic append, reset, replacement, and compaction mutations.
- Usage reconciliation and signed variance.

### Lineage Tests

- A root response followed by several tool-result continuations.
- A new root turn over the same pooled connection.
- Missing predecessor.
- Predecessor arriving after its child.
- Forked `previous_response_id` chains.
- Main agent and subagent calls.
- Connection close during an invocation.
- Provider failure after partial output.
- Compaction or opaque provider context.

### Persistence and Migration Tests

- Snapshot content deduplication.
- Logical aggregate updates after each invocation.
- Historical backfill with retained bodies.
- Historical rows with purged bodies.
- Retention cleanup of snapshot content.
- Migration idempotency.

### API and UI Tests

- REST request appears as one logical request with one invocation.
- Multi-call Codex turn appears as one logical request.
- Physical invocation APIs remain available.
- Filtering and sorting operate on logical aggregates.
- Live updates modify an existing logical row.
- Error filtering handles WebSocket status correctly.
- Low-confidence and incomplete reconstructions are visibly labeled.

### Extensibility Test

Implement a small fixture-only second conversation adapter with different metadata names and continuation rules. It should integrate without changes to the context engine, persistence pipeline, logical API, or UI.

## Rollout Order

1. Add real Codex fixtures and fix custom tool parsing.
2. Introduce normalized identity, mutation, and integration interfaces.
3. Add schema and persist lineage without changing the UI.
4. Build context snapshots and token reconciliation.
5. Add logical-request APIs.
6. Switch the UI to logical rows with expandable invocations.
7. Add historical backfill.
8. Add the fixture second provider and document the extension contract.

Feature flags can keep logical grouping and context reconstruction independently switchable during development.

## Risks and Mitigations

### Exact Context May Be Unobservable

Server-managed instructions, hidden reasoning, encrypted state, and compaction may prevent exact reconstruction.

Mitigation: report reconstructed visible context, provider-reported input, coverage, and unattributed input separately.

### Incorrect Grouping

Metadata may be absent or change between Codex versions.

Mitigation: prefer explicit root-turn metadata, record grouping confidence, retain physical rows, and never group solely by connection.

### Provider Semantics May Differ

A provider may replace history, summarize it, or use opaque state instead of append-only continuation.

Mitigation: make adapters return generic context mutations instead of assuming append-only history in shared code.

### Retention and Privacy

Reconstructed snapshots may keep references to content longer than expected.

Mitigation: apply the same retention and garbage-collection rules to snapshot references and content-addressed blocks.

### Aggregate Terminology

Summed token usage can be mistaken for one model context window.

Mitigation: consistently distinguish peak context, final context, and cumulative input processed.

## Acceptance Criteria

The implementation is complete when:

- One Codex user turn appears as one top-level logical request.
- Every actual model invocation remains inspectable.
- Standard REST requests use the same model and normally contain one invocation.
- Codex custom tool calls and results are captured and linked.
- Every physical invocation shows observed, reconstructed, and provider-reported input.
- Server-managed or otherwise unavailable context is represented as unattributed input.
- Logical summaries distinguish peak/final context from cumulative token usage.
- Cache-read, cache-write, output, and reasoning usage are reported when supplied.
- Missing lineage and incomplete captures degrade gracefully without losing the physical request.
- Historical rows are backfilled when retained data permits it.
- Adding a provider with a new WebSocket and conversation schema requires adapters and registration, not a refactor of the core pipeline.

## References

- [OpenAI conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [OpenAI Responses WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode)
- [OpenAI Responses API reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [OpenAI model and token-usage guidance](https://developers.openai.com/api/docs/guides/latest-model)
