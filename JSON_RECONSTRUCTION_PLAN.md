# Canonical JSON reconstruction and transport/analysis separation

## Status and ordering

This plan is the first of two related implementation plans. It must be completed before
`SESSION_ANALYSIS_PLAN.md` begins.

The purpose of this refactor is to ensure that HTTP JSON, SSE, and WebSocket traffic all cross the
same analysis boundary: a canonical provider request/response JSON document. Transport handling
may normalize framing, whitespace, compression, and chunk boundaries, but it must not discard
semantically meaningful payload data merely because ContextSpy does not currently map it to a
block or usage field.

## Goals

1. Separate transport capture and decoding from provider response reconstruction and block
   analysis.
2. Reconstruct streamed responses into the provider's normal non-streaming response shape.
3. Pass the same canonical request/response JSON that is persisted and shown in the formatted
   raw view into `parse_request()` and `parse_response()`.
4. Preserve all semantically meaningful request and response information, including fields and
   event types that ContextSpy does not currently analyze.
5. Make equivalent streaming and non-streaming responses produce equivalent blocks and usage.
6. Keep capture successful when reconstruction or block analysis fails.
7. Preserve the current retention policy and make any normalized/reconstructed status visible in
   the API/UI.

## Non-goals

- Byte-for-byte storage of compressed bodies, HTTP chunks, TLS records, or WebSocket framing.
- Preserving insignificant JSON whitespace, key formatting, or SSE line formatting.
- Reconstructing historical SSE/WS payloads that were already replaced by synthetic responses.
- Changing classification rules, token-counting rules, or category aggregation except where a
  bug is exposed by routing all transports through `parse_response()`.
- Implementing the session-analysis screen; that is covered by `SESSION_ANALYSIS_PLAN.md`.

## Required invariants

The implementation should enforce these invariants in code and tests:

1. **Capture is authoritative.** Raw/canonical payload persistence does not depend on successful
   adapter analysis.
2. **One parsing path.** Blocks and usage are derived through `parse_request(request_json)` and
   `parse_response(response_json)` for every transport.
3. **Streaming equivalence.** A streamed response and its equivalent buffered response reduce to
   semantically equivalent canonical JSON and produce the same blocks/usage.
4. **Unknown data survives.** Reconstruction preserves unknown event fields in the normalized
   capture even when they are not folded into known provider response fields.
5. **Ordering survives.** SSE events and WebSocket frames retain their original application-level
   order wherever ordering has semantic meaning.
6. **No synthetic raw response.** A response assembled from analyzed blocks is never stored as
   raw/canonical response data.

## Current behavior and gaps

- HTTP request bodies are decoded with `flow.request.get_text()`, parsed with `json.loads()`, and
  the same decoded text is stored in `raw_request_body`.
- Buffered JSON responses are stored in full and parsed through `parse_response()`.
- SSE responses are parsed directly into blocks by `parse_sse()` and then replaced in storage by
  `_synthetic_response_text()`. This loses tool calls, lifecycle events, annotations, finish
  details, provider-specific usage, errors, and unknown fields.
- WebSocket response events are parsed through `parse_events()` and also replaced by
  `_synthetic_response_text()`.
- The Codex WebSocket session filters some control events and reduces error events before the raw
  response is stored.
- Normal HTTP requests are persisted only after a response arrives. A connection failure can
  therefore lose the captured request entirely.
- `extract_sse_events()` only recognizes single `data: ` lines, skips malformed/unknown data, and
  discards SSE `event`, `id`, `retry`, comments, and multiline `data` structure.

## Target architecture

```text
HTTP/JSON body ───────────────────────────────────────────────┐
                                                            │
SSE bytes → generic SSE decoder → ordered transport events ─┤
                                                            ├─→ provider response assembler
WS frames → generic frame capture → ordered transport events┘              │
                                                                           ▼
                                                              canonical response JSON
                                                                           │
                                      ┌────────────────────────────────────┼─────────────┐
                                      ▼                                    ▼             ▼
                                  persistence                     parse_response()   formatted raw view
                                                                           │
                                                                           ▼
                                                                    blocks and usage
```

The generic transport decoder understands only framing. Provider-specific event reduction belongs
to the wire-format adapter layer because event types and accumulation rules differ across
Anthropic Messages, OpenAI Chat Completions, OpenAI Responses, and Ollama.

## Canonical capture model

Introduce transport-neutral in-memory types, preferably in a new module such as
`contextspy/proxy/capture.py`:

```python
@dataclass
class CapturedEvent:
    sequence: int
    direction: str                 # client_to_server | server_to_client
    kind: str                      # json | text | binary
    payload: object | None         # complete decoded JSON when available
    text: str | None = None        # original decoded application text when useful
    event: str | None = None       # SSE event field
    event_id: str | None = None    # SSE id field
    retry_ms: int | None = None

@dataclass
class CanonicalResponse:
    payload: dict                  # provider's canonical non-streaming response JSON
    transport: str                 # json | sse | websocket
    events: list[CapturedEvent]    # complete normalized event/frame record for streamed traffic
    reconstructed: bool
    complete: bool = True
    error: dict | None = None
```

The exact class names may change, but the separation between canonical provider payload and the
complete normalized transport-event record should remain. `payload` is the object consumed by
`parse_response()`. `events` preserves information that has no natural place in the provider's
buffered response schema or is not yet understood by the reducer.

For ordinary buffered JSON, `events` may be empty and `reconstructed` is false. For SSE/WS,
`payload` is reconstructed and `events` contains the complete ordered normalized capture.

## Persistence format

### Request

Continue storing the complete decoded request JSON in `raw_request_body`. Parse it once and pass
the resulting object to `parse_request()`. Do not reserialize before storage unless a later schema
decision explicitly renames this field to `canonical_request_body`.

### Response

Store two logically distinct things:

1. `canonical_response_body`: JSON serialization of `CanonicalResponse.payload`; this is the
   exact JSON passed to `parse_response()` and used by the formatted response view.
2. `response_events`: normalized JSON serialization of `CanonicalResponse.events` for SSE/WS;
   nullable for buffered responses.

Keep `raw_response_body` as a compatibility alias during the transition, or migrate it to mean
`canonical_response_body` consistently. Do not overload one field with provider JSON for buffered
responses and an unrelated event-array shape for streamed responses without also returning an
explicit format field.

Add request-level metadata:

- `response_transport`: `json`, `sse`, `websocket`, or `text`.
- `response_reconstructed`: boolean.
- `response_complete`: boolean.

The preferred schema is explicit new columns because canonical response JSON and normalized
transport events have different consumers and retention behavior. All new columns on existing
tables must be added to the additive migration logic in `contextspy/db/database.py`. Generalize
that migration list to include a table name instead of assuming every column belongs to
`requests`.

A data migration cannot recover old SSE/WS events from existing synthetic bodies. Existing rows
should retain their old response body, receive `response_reconstructed = false` (or an explicit
`legacy` format), and not be presented as complete captures. A schema-version bump is needed only
if existing rows require derived/backfilled values beyond safe column defaults.

## Adapter contract refactor

Update `WireFormatAdapter` in `contextspy/analysis/adapters/base.py` to separate response assembly
from analysis:

```python
def reconstruct_response(
    self,
    events: list[CapturedEvent],
    *,
    transport: str,
) -> CanonicalResponse:
    ...

def parse_response(self, resp_body: dict) -> tuple[list[Block], Usage]:
    ...
```

`parse_request()` remains unchanged except for the later JSON-path work described in the session
analysis plan.

Deprecate and then remove analysis-producing `parse_sse()` and `parse_events()`. During an
incremental migration, their temporary implementation may be:

```python
canonical = self.reconstruct_response(events, transport="sse")
return self.parse_response(canonical.payload)
```

No adapter should maintain separate block-construction logic for buffered, SSE, and WebSocket
responses after the refactor is complete.

## Generic SSE decoding

Replace `extract_sse_events()` with a standards-compatible decoder that:

- Groups records by blank-line boundaries.
- Supports multiline `data:` fields.
- Accepts `data:` both with and without a following space.
- Preserves `event`, `id`, and `retry` fields.
- Preserves event ordering.
- Recognizes `[DONE]` without silently discarding the fact that it occurred.
- Parses JSON data when possible.
- Preserves non-JSON or malformed data as text rather than dropping it.
- Handles comments without treating them as provider payload; retain them only if they may carry
  observable semantic information.

The decoder should return `CapturedEvent` values and contain no provider-specific event logic.

## Provider-specific reconstruction

### Anthropic Messages

Reconstruct the normal Messages response shape:

- Top-level message ID, type, role, model, stop reason, and stop sequence.
- Ordered content blocks by index.
- Text, thinking, redacted-thinking, and tool-use content.
- Tool-call IDs, names, and incrementally accumulated input JSON.
- Complete usage, including cache fields and provider additions.
- Error events and incomplete-stream status.

### OpenAI Chat Completions

Reconstruct the normal chat completion object:

- ID, object, created time, model, system fingerprint, and service tier when present.
- Every choice by index, not only choice zero.
- Role, content, refusal, reasoning content, annotations, audio metadata, and finish reason.
- Tool/function calls by index with incrementally accumulated arguments.
- Complete usage and nested token-detail objects.
- Provider-specific top-level and choice-level fields.

### OpenAI Responses

Reconstruct the normal Responses API object:

- Full response lifecycle fields and status/error/incomplete details.
- Ordered output items and content parts by their provider IDs/indexes.
- Output text, refusal, reasoning summaries, function calls, arguments, and annotations.
- Usage with input/output/reasoning/cache detail objects.
- All fields present on `response.created`, `response.completed`, or equivalent full snapshots.

Prefer the provider's completed response snapshot when it is present, while still retaining every
event in `CanonicalResponse.events`. Apply deltas only where the snapshot is absent or incomplete.

### Ollama

Reconstruct the normal Ollama response object:

- Message role/content/thinking/tool calls for chat endpoints.
- Generated response for generate endpoints.
- Model, timestamps, done state/reason, context, and duration/evaluation fields.
- All unknown fields carried by terminal chunks.

## Proxy pipeline refactor

Refactor `contextspy/proxy/addon.py` so each transport follows the same orchestration:

1. Capture and decode the request body.
2. Persist or retain enough state to persist it even if the upstream call fails.
3. Capture the complete buffered body or normalized ordered stream events.
4. Ask the adapter to reconstruct canonical response JSON for streamed traffic.
5. Persist canonical request/response data and event data independently of block parsing success.
6. Call `parse_request()` and `parse_response()` on those same canonical JSON objects.
7. Classify and persist blocks/usage if parsing succeeds.
8. Broadcast the completed request record.

Delete `_synthetic_response_text()` once no path calls it.

For HTTP connection errors, extend the normal `error()` hook to persist the request with no
response payload and structured transport-error metadata. Guard against double persistence when a
response and an error hook both fire by recording a per-flow saved flag or capture state.

## WebSocket changes

Refactor `CompletedExchange` and `WsSession` so raw frame collection is unconditional and protocol
interpretation is additive:

- Retain every relevant client/server frame in order before attempting JSON parsing.
- Do not remove rate-limit, control, error, or unknown JSON frames from the normalized capture.
- Continue identifying the request-start and response-completion boundaries in the protocol
  implementation.
- Attach out-of-band frames to the active exchange when they arrive during it.
- Decide explicitly how idle connection-level events are represented. They should not be silently
  attached to an unrelated request; a connection-level capture record or explicit omission policy
  is preferable.
- Preserve errors in full while also deriving selected status/error fields for request metadata.
- Keep incomplete exchanges and mark them as incomplete rather than dropping them.

The existing trimming of mitmproxy's in-memory WebSocket history may remain after the frame has
been copied into the exchange capture.

## API and UI adjustments

Extend the request API type with response capture metadata and, when requested, normalized events.
Avoid loading large event logs in list endpoints.

The request detail response viewer should distinguish:

- `JSON`: formatted canonical provider response JSON.
- `Events`: complete normalized SSE/WS events, shown only when present.
- `Text` and `Thinking`: derived block views.
- `Raw text`: optional decoded body view for non-JSON fallback data.

The word “Raw” should be documented as “complete normalized application payload,” not wire bytes.
Unknown fields must remain visible in JSON/Events views even if no block references them.

## Retention

Apply `raw_body_days` consistently to canonical request/response bodies and response event data.
Do not purge canonical bodies while retaining an event log that contains the same sensitive
content, or vice versa. Update `startup_vacuum()` and DB statistics accordingly.

The default seven-day retention may remain. The UI must clearly distinguish “purged by retention”
from “capture/reconstruction failed.”

## Implementation sequence

1. Add capture/event dataclasses and the generic SSE decoder.
2. Add reconstruction fixtures and golden canonical-response fixtures for every provider.
3. Implement `reconstruct_response()` provider by provider without changing the live proxy path.
4. Prove streamed/non-streamed block equivalence by feeding reconstructed JSON into existing
   `parse_response()`.
5. Add persistence columns and migrations for canonical response/event metadata.
6. Refactor the HTTP SSE path to persist and analyze canonical reconstructed JSON.
7. Refactor the buffered HTTP path to use the same orchestration.
8. Refactor the WebSocket exchange collector and canonical reconstruction path.
9. Add HTTP error-path capture and double-save protection.
10. Update request APIs and `RawViewer`/response tabs.
11. Remove `_synthetic_response_text()`, `parse_sse()`, and `parse_events()` after all callers and
    tests have migrated.
12. Update `SPEC.md`, `docs/development.md`, retention documentation, and changelog.

## Tests

### Transport decoder tests

- SSE records with and without spaces after `data:`.
- Multiline data, event IDs, retry fields, comments, `[DONE]`, malformed JSON, and non-JSON data.
- Ordering across arbitrary chunk boundaries.
- UTF-8 replacement behavior is explicit and does not crash capture.
- WebSocket text, unknown JSON, errors, rate limits, incomplete exchanges, and optional binary
  frames.

### Reconstruction tests

- Golden event stream → expected canonical provider response for each provider.
- Multiple output choices/items/content blocks.
- Incremental tool-call arguments.
- Thinking/reasoning/refusal/annotation fields.
- Complete usage/cache/token detail objects.
- Unknown event fields survive in the normalized event capture.
- Terminal full-response snapshots take precedence without losing retained event data.

### Equivalence tests

For every provider fixture:

```python
streamed = adapter.reconstruct_response(events, transport="sse").payload
buffered = equivalent_buffered_response

assert adapter.parse_response(streamed) == adapter.parse_response(buffered)
```

Compare block types, content, tool attribution, token counts, attrs, output split, and usage—not
only concatenated response text.

### Persistence/API tests

- Capture persists when adapter reconstruction or parsing raises.
- Buffered response returns complete canonical JSON.
- SSE/WS response returns canonical JSON plus complete normalized events.
- HTTP connection error retains the request payload.
- Retention purges canonical bodies and event logs consistently.
- Legacy synthetic rows are identified and do not claim complete event capture.

### Verification commands

```bash
pytest
cd ui && npm run build
```

Manually inspect one buffered, one SSE, and one WebSocket request in the UI and verify that:

1. Unknown fields are visible.
2. Tool calls and usage details are complete.
3. Parsed blocks match the canonical JSON.
4. Event ordering is visible for streamed responses.
5. Purged, incomplete, failed, and reconstructed states are distinguishable.

## Completion criteria

This refactor is complete when:

- No live path stores `_synthetic_response_text()` output.
- Every transport produces canonical provider response JSON before block analysis.
- All blocks and usage come from `parse_request()`/`parse_response()` over persisted canonical JSON.
- Streamed and buffered equivalents yield the same analysis.
- Semantically meaningful unknown data is retained in canonical payloads or normalized events.
- Failed/incomplete captures remain inspectable.
- Existing tests pass and the UI builds successfully.

