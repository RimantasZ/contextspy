# REST, streaming, and WebSocket request handling

ContextSpy analyzes provider invocations rather than network messages. The request list and
request-detail view therefore use the same provider-shaped JSON representation whether traffic
arrived as buffered REST, an HTTP event stream, or WebSocket frames.

## What creates a request row?

One row represents one externally observable provider invocation and its terminal usage record.

- A buffered HTTP request/response pair creates one row.
- An SSE or NDJSON stream creates one row after its events are reduced into a complete response.
- For supported WebSocket protocols, one provider start-to-terminal lifecycle creates one row.
- Streaming deltas, rate-limit updates, timing messages, and ping/pong frames do not create rows.

For OpenAI Responses traffic, a lifecycle normally starts with `response.create` and ends with
`response.completed`, `response.failed`, or `response.incomplete`. An agent tool loop can contain
several such lifecycles, and therefore several rows, even when it began with one user prompt.

ContextSpy counts the invocations visible through the provider protocol. It cannot determine
whether the provider performed additional private sampling or processing passes internally.

## Canonical request and response JSON

Before analysis, every supported invocation is converted into two transport-neutral documents:

- the **canonical request** is the provider-shaped context submitted for that invocation;
- the **canonical response** is the provider-shaped response, including output and terminal
  usage.

For buffered REST JSON, the original JSON text can be used directly. For SSE, NDJSON, and
WebSocket traffic, ordered events are reduced into the equivalent buffered provider response
object. The normal request viewer displays these canonical documents and does not require the UI
to interpret frames or transport types.

Blocks, token categories, tool statistics, output text, visible thinking, and usage columns are
all derived from the canonical documents. Retaining those documents makes it possible to replay
analysis without reconstructing the transport stream again.

## Stateful requests and accumulated context

Some Responses API requests contain only the newest input plus a `previous_response_id` instead
of resending the full conversation. ContextSpy follows that exact provider-issued ID and expands
the visible input as:

```text
predecessor canonical input
+ predecessor canonical output
+ current observed input
```

This produces the same context progression that an explicitly expanded REST conversation would
show. ContextSpy never infers a predecessor from row order, timestamps, or WebSocket connection
identity.

If an explicitly referenced predecessor is unavailable, the context is marked **partial**. If
the provider supplies compacted, encrypted, or otherwise non-inspectable state, it is marked
**opaque**. The visible provider item is retained, but ContextSpy does not fabricate its original
contents.

## Why can a reconstructed Codex response look large?

Response JSON size is provider-schema dependent and is not the same thing as generated output
size. OpenAI Responses objects used by Codex can echo invocation configuration such as:

- instructions;
- tool definitions and tool-selection settings;
- reasoning and text configuration;
- cache, safety, service, and client metadata.

Those fields can make a canonical response tens of kilobytes even when the model generated only
a few tokens. Anthropic Messages responses are usually much smaller because they generally do
not echo the request's instructions and complete tool schema.

This is expected provider response content, not repeated output-delta reconstruction. ContextSpy
retains it so the reconstructed response remains lossless and useful for debugging.

The size of `canonical_response_body` must not be interpreted as billed output. Provider-reported
`output_tokens` and `reasoning_tokens` describe output accounting for the invocation. Instructions
and tool definitions belong to the request context and are reflected in provider input and cache
usage when applicable.

## What is stored?

The request record may contain several related artifacts:

| Field | Purpose |
| --- | --- |
| `canonical_request_body` | Exact provider-shaped request analyzed and displayed |
| `canonical_response_body` | Exact provider-shaped response analyzed and displayed |
| `raw_request_body` | Original REST request or sparse WebSocket start message |
| `raw_response_body` | Compatibility copy of the processed response |
| `response_events` | Optional ordered streaming/WebSocket diagnostics |
| blocks and category columns | Derived indexes used by analysis and the UI |

For a reconstructed WebSocket invocation, `raw_response_body` may currently equal
`canonical_response_body`, while `response_events` separately retains the larger event history.
This intentional compatibility duplication affects local database size, not token accounting.
All of these sensitive payloads follow the configured raw-body retention policy.

## Usage and visible composition

Provider usage is the accounting source for billing-related analysis. Canonical request
composition is the explanation source for what ContextSpy can inspect.

ContextSpy reports these values separately:

- locally tokenized visible input composition;
- provider-reported input and output tokens;
- cached-input and cache-write tokens when supplied;
- provider-reported reasoning tokens when supplied;
- the unattributed/tokenizer difference between provider input and visible local composition.

That difference can include opaque state, provider tokenizer differences, server transformations,
or parsing gaps. It is deliberately not labelled as hidden context unless the provider schema
actually establishes that fact.

