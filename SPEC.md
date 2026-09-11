# ContextSpy — Technical Specification v0.3.5

> **Status:** Implemented — updated September 2026
> **Purpose:** Living specification — reflects the currently running codebase.

---

## 1. Overview

ContextSpy is a local proxy that sits between LLM coding agents (GitHub Copilot, Claude, opencode, OpenAI SDK clients) and their provider APIs — either cloud APIs or local LLM servers. It captures every LLM request, analyses the composition of the context window, counts tokens per category, persists statistics in a local SQLite database, and serves a React web dashboard with charts and per-request drill-downs.

ContextSpy operates in two complementary modes:

- **Cloud / forward-proxy mode** (`contextspy start`): acts as an HTTPS MITM proxy for requests to cloud LLM APIs (OpenAI, Anthropic, GitHub Copilot, etc.). Requires the mitmproxy CA certificate to be installed in the OS trust store.
- **Local / reverse-proxy mode** (`contextspy start-local`): acts as a plain-HTTP reverse proxy in front of local LLM servers (llama.cpp/llama-server, Ollama, vLLM, etc.). No TLS, no certificate installation — the client simply changes its base URL to point at the ContextSpy listener port.

**Core value:** answer the question *"where are my tokens actually going?"* — how much of each context window is system prompt, MCP tool definitions, tool call results, file contents, conversation history, etc.

---

## 2. Goals

- Intercept and parse LLM API requests/responses transparently, with no modifications to agent behaviour.
- Classify each context window into meaningful content categories.
- Count tokens per category using `tiktoken` (fast approximation, good enough for composition analysis).
- Support named sessions so the user can group requests into logical work units.
- Purge retained payloads and block text on startup after their configured retention windows,
  while keeping token stats and structural metadata.
- Display statistics and graphs in a browser UI served locally.
- Bind network services to `127.0.0.1` by default.

---

## 3. Non-Goals (v1)

- Production / multi-user deployment.
- Exact token counts via provider-native tokenizer APIs.
- Supporting wire formats beyond OpenAI Chat Completions, OpenAI Responses, Anthropic Messages,
  and Ollama's native chat/generate endpoints.
- Modifying or blocking intercepted traffic.
- Authentication or authorisation on the web UI.

---

## 4. Architecture

### 4.1 Cloud / Forward-Proxy Mode

```
┌──────────────────────────────────────────────────────────────┐
│  Coding Agent (Copilot / Claude / opencode / openai SDK)     │
└───────────────────────┬──────────────────────────────────────┘
                        │  HTTPS
                        │  via HTTPS_PROXY=http://127.0.0.1:8888
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  ContextSpy Proxy  (mitmproxy, 127.0.0.1:8888)               │
│  • TLS termination via local CA cert                         │
│  • Filters to known LLM hostnames only                       │
│  • Normalizes supported HTTP and WebSocket invocations       │
│  • Writes records to SQLite                                  │
└───────────────────────┬──────────────────────────────────────┘
                        │  forwards original HTTPS request
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  LLM Provider API                                            │
│  (api.openai.com, api.anthropic.com, chatgpt.com, …)         │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Local / Reverse-Proxy Mode

```
┌──────────────────────────────────────────────────────────────┐
│  Client app (opencode / script / SDK)                        │
│  base_url = http://127.0.0.1:8889/v1   (ContextSpy port)     │
└───────────────────────┬──────────────────────────────────────┘
                        │  plain HTTP  (no TLS, no proxy env var)
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  ContextSpy Reverse Proxy (mitmproxy, 127.0.0.1:8889)        │
│  mode = reverse:http://127.0.0.1:8080                        │
│  • provider_override supplies the stored provider label      │
│  • endpoint path selects the wire-format adapter             │
│  • Normalizes supported HTTP invocations                     │
│  • Writes records to SQLite                                  │
└───────────────────────┬──────────────────────────────────────┘
                        │  plain HTTP forwarded to local server
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  Local LLM Server (llama-server / Ollama / vLLM)             │
│  127.0.0.1:8080  (or whatever port the server uses)          │
└──────────────────────────────────────────────────────────────┘
```

Multiple `[[reverse_targets]]` can be configured simultaneously — each gets its own mitmproxy DumpMaster on a separate listen port.

### 4.3 Shared Infrastructure

```
┌──────────────────────────────────────────────────────────────┐
│  ContextSpy Web Server  (FastAPI + Uvicorn, 127.0.0.1:5173)  │
│  • REST API for sessions, requests, stats, proxy control     │
│  • WebSocket endpoint for live updates                       │
│  • Serves built React UI as static files                     │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  SQLite Database  (~/.contextspy/contextspy.db)               │
└──────────────────────────────────────────────────────────────┘
```

Both modes share the same database, web server, and dashboard. The proxy addon and FastAPI backend run **in the same Python process**: mitmproxy runs via its async API in a dedicated thread (one per target in local mode); FastAPI/Uvicorn runs in the main asyncio event loop.

---

## 5. Components

### 5.1 HTTPS Proxy (Cloud / Forward Mode)

**Technology:** `mitmproxy` Python library (inline addon API).  
**Port:** `8888` (configurable via `--proxy-port`).  
**Bind address:** `127.0.0.1` by default (configurable via `[proxy].bind_addr`).

#### TLS Interception

- On first `ContextSpy start`, check for `~/.mitmproxy/mitmproxy-ca-cert.pem`; mitmproxy generates it automatically if absent.
- Detect OS and attempt automatic CA trust-store installation:
  - **Windows:** `certutil -addstore Root ~/.mitmproxy/mitmproxy-ca-cert.pem`
  - **macOS:** `security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.mitmproxy/mitmproxy-ca-cert.pem`
  - **Linux:** copy to `/usr/local/share/ca-certificates/mitmproxy-ca.crt` + `sudo update-ca-certificates`
- If automatic installation fails, print clear manual instructions and continue.
- mitmproxy dynamically signs per-domain certificates using the local CA — no external CA needed.
- The `ContextSpy install-cert` CLI command re-runs this step independently.

#### Hostname Filter

Only analyze and store flows attributed to the following hosts/ports. Other proxy traffic is
forwarded without a ContextSpy record:

| Hostname pattern | Provider |
|---|---|
| `api.openai.com` | `openai` |
| `openai.azure.com` and subdomains | `openai_azure` |
| `api.anthropic.com` | `anthropic` |
| `copilot-proxy.githubusercontent.com`, `githubcopilot.com` and subdomains | `copilot` |
| `opencode.ai`, `*.opencode.ai` | `opencode_zen` |
| `chatgpt.com`, `*.chatgpt.com` | `openai_chatgpt` |
| any host on port `11434` | `ollama` |

#### Addon Class: `ContextSpyAddon`

Accepts an optional `provider_override: str | None` constructor argument. When set, the addon skips hostname-based provider detection and always uses the specified provider string. Used by reverse-proxy mode where the upstream is a known local server.

The addon handles regular JSON, SSE/NDJSON streams, transport failures, and registered WebSocket
protocols. Request bodies are captured before forwarding so a connection failure can still
produce an inspectable request row. Host detection first selects a provider label; endpoint
matching then selects the wire-format adapter and prevents unrelated telemetry/auth traffic on a
known host from being stored. Stream framing is decoded separately from provider reconstruction
and analysis:

```
class ContextSpyAddon:
    def __init__(self, provider_override=None):
        ...
    def _get_provider(self, host, port):
        if self._provider_override:
            return self._provider_override
        return _detect_provider(host, port)

    def request(self, flow):
        flow.metadata["ts_start"] = time.monotonic()
        # Retain the decoded application request body for success/error paths.

    def responseheaders(self, flow):
        # For text/event-stream responses, attach a streaming callback
        # that collects SSE chunks. When the stream ends (empty bytes
        # sentinel), decode complete SSE records, reconstruct canonical provider
        # response JSON, and save both the canonical JSON and normalized events.
        # Sets flow.metadata["is_sse"] = True to suppress response().

    def response(self, flow):
        # Skipped if is_sse is set (handled by stream callback).
        # Otherwise:
        # 1. Detect provider from hostname/port; skip unknown providers
        # 2. Extract request/response application payloads
        # 3. Select an adapter from the endpoint path
        # 4. Detect agent from User-Agent
        # 5. Reconstruct SSE-like/NDJSON response JSON when needed
        # 6. Normalize provider state and analyze the canonical JSON
        # 7. Persist the request, blocks, usage, and transport diagnostics
        # 8. Emit a WebSocket event for live UI update

    def websocket_start(self, flow):
        # Attach a registered per-connection protocol session.

    def websocket_message(self, flow):
        # Feed frames to the session assembler. Each completed provider
        # start-to-terminal exchange becomes one stored request.

    def websocket_end(self, flow):
        # Flush a dangling exchange as incomplete.

    def error(self, flow):
        # Store a failed/incomplete invocation even when there is no response.
```

#### mitmproxy Runner (`proxy/runner.py`)

- `DumpMaster` is constructed **inside** the proxy thread after `asyncio.new_event_loop()` is set, with `loop=loop` passed explicitly to avoid `RuntimeError: no running event loop`.
- The `ErrorCheck` addon (which calls `sys.exit(1)` on port-bind failure) is removed from the addon chain after construction to prevent it from killing the uvicorn process.
- A `_BindWatcher` logging handler watches the mitmproxy logger for `"listening at"` / `"failed to listen"` messages and sets a `_bound` flag accordingly.
- `is_running()` returns `True` only when `_bound` is `True` **and** the proxy thread is alive.
- `with_termlog=False, with_dumper=False` suppresses mitmproxy's own log/dump addons.

---

### 5.1b Reverse Proxy (Local / `start-local` Mode)

**Technology:** `mitmproxy` `DumpMaster` in `reverse:` mode — one instance per `[[reverse_targets]]` entry.  
**Ports:** Configured per-target in `config.toml` (e.g. `8889`, `8890`).  
**TLS:** None — the upstream is plain HTTP on localhost.  
**Provider detection:** Bypassed — `provider_override` supplies the stored provider label and
allows the local flow through the provider gate. The request path still selects the adapter.

Each reverse target is described by:

| Field | Type | Description |
|---|---|---|
| `name` | str | Human label (e.g. `"llama-server"`) |
| `listen_port` | int | Port contextspy binds (e.g. `8889`) |
| `target_url` | str | Upstream URL (e.g. `"http://127.0.0.1:8080"`) |
| `provider` | str | Provider label stored on captures; defaults to `"openai"` and may be set to `"anthropic"` or `"ollama"` |

All three local server types expose an OpenAI-compatible `/v1/chat/completions` endpoint, so `provider = "openai"` is the correct choice for llama-server, Ollama (`/v1` endpoint, requires Ollama ≥ 0.1.24), and vLLM.

`start_local_proxies(settings, ws_manager)` in `contextspy/proxy/runner.py` iterates over `settings.reverse_targets` and spawns one daemon thread per target. `stop_local_proxies()` shuts them all down cleanly.

---

### 5.2 Context Analyser

Runs synchronously after each completed or failed captured invocation, entirely in the backend.
The frontend may pretty-print retained JSON for display, but it does not classify blocks or
derive token/category/tool aggregates. The pipeline is provider-agnostic: a **wire-format
adapter** turns provider-specific JSON into a small set of domain types, and a single classifier
operates on those types regardless of which provider or wire format produced them.

#### Domain Model (`analysis/blocks.py`)

- **`Block`** — one content-addressable unit of context: a system prompt, a tool definition, a
  message, a tool call, a tool result, or a thinking/reasoning segment. Fields: `direction`
  (`input`/`output`), `block_type` (see `BlockType` below), `content`, `position` (order within
  its direction), `message_index` (order of the conversational turn it belongs to, or `-1`/`None`
  for turn-independent blocks like the system prompt), `category` (assigned by the classifier,
  input blocks only), `content_hash` (sha256, auto-computed by `Block.make()`), `token_count`
  (auto-computed via the tokenizer unless a provider-reported count is passed explicitly),
  `tool_name`, `tool_call_id`, and a free-form `attrs` dict (e.g. `{"is_prefill": true}`).
- **`BlockType`**: `system_prompt`, `tool_definition`, `user_message`, `assistant_message`,
  `tool_call`, `tool_result`, `assistant_prefill` (reserved — prefill is currently expressed via
  `attrs["is_prefill"]` on an `assistant_message` block, not this type), `thinking`, `other`.
- **`Usage`** — provider-reported token counts when available: `input_tokens`, `output_tokens`,
  `reasoning_tokens`, `cache_read_tokens`, `cache_creation_tokens`, plus a free-form `extra` dict
  for provider-specific usage fields that don't map to the others.
- **`AnalyzedRequest`** — the adapter's output: `model`, `input_blocks: list[Block]`,
  `output_blocks: list[Block]`, `usage: Usage`, `tool_call_map` (tool_call_id → tool_name, used to
  attribute tool-result tokens back to a tool name for per-tool stats). Replaces the old
  monolithic `ParsedRequest`.

#### Wire-Format Adapters (`analysis/adapters/`)

Each supported wire format is one `WireFormatAdapter` subclass implementing
`parse_request(req_body) -> (input_blocks, tool_call_map)`,
`reconstruct_response(events, transport) -> CanonicalResponse`, and
`parse_response(resp_body) -> (output_blocks, usage)`. `parse_response()` is the only response
analysis path: SSE, NDJSON, and WebSocket events are first reduced to the provider's normal
buffered response shape. `get_adapter(endpoint)` dispatches
by matching the request **path** (not host) against each adapter's `endpoint_patterns`, first
match wins:

| Adapter | `format_id` | `endpoint_patterns` |
|---|---|---|
| `anthropic.py` | `anthropic` | `/messages` |
| `openai_chat.py` | `openai_chat` | `/chat/completions`, `/completions` |
| `openai_responses.py` | `openai_responses` | `/responses` |
| `ollama.py` | `ollama` | `/api/chat`, `/api/generate` |

Adapters are registered in this order in `analysis/adapters/__init__.py`; order matters where
patterns could otherwise overlap. There is no adapter per *provider* — OpenAI-compatible
providers (Azure OpenAI, GitHub Copilot, opencode's cloud API) are detected separately by host
(`proxy/addon.py`'s `_HOST_PROVIDER`, used only to label `provider`/`agent` on the stored
request) but parsed by the same `openai_chat`/`openai_responses` adapter, since they speak the
same wire format. Adding a genuinely new wire format is a new adapter module and a `register()`
call — nothing else in the pipeline changes.

Requests to unrecognised endpoints (`get_adapter` returns `None`) are normally skipped. If the
path still resembles a supported LLM endpoint, the transport evidence is retained with no block
rows and zero local token totals so parser/capture failures remain inspectable.

#### Canonical invocation normalization

Because Claude Code and most modern LLM clients use streaming responses
(`Content-Type: text/event-stream`), the `response()` mitmproxy hook never fires for these flows.
Instead `responseheaders()` detects `text/event-stream` and attaches a streaming callback that
accumulates raw SSE bytes; on stream end, `proxy/addon.py`'s `_handle_sse_response` decompresses
the body and uses the transport-neutral decoder in `analysis/capture.py`. The decoder preserves
ordered application records, multiline `data`, SSE metadata, non-JSON text, and `[DONE]` before
the adapter reconstructs canonical provider response JSON. Ollama's JSON-lines streams and Codex
WebSocket events follow the same boundary.

The current WebSocket registry contains one protocol: `codex_responses`, matching
`chatgpt.com/backend-api/codex/responses`. It assembles each `response.create` plus server events
through `response.completed`, `response.failed`, or `response.incomplete`; connection closure or a
superseding request flushes a pending exchange as incomplete. Auxiliary, text, and binary frames
remain in `response_events`, while idle rate-limit frames outside an invocation do not create rows.

Transport ingestion emits one observed invocation, not one row per frame. Provider normalization
then resolves explicit state references such as Responses API `previous_response_id` and emits a
standalone `CanonicalInvocation`. A resolved continuation expands to predecessor canonical input
+ predecessor canonical output + current input. The same normalizer applies to stateful REST and
WebSocket traffic; only explicit provider IDs establish lineage.

`canonical_request_body` and `canonical_response_body` store the exact JSON documents supplied to
`parse_request()`/`parse_response()`. Blocks, category totals, tools, output, thinking, and usage
are derived from those documents once. `raw_request_body` retains observed transport evidence,
`raw_response_body` remains a compatibility field, and `response_events` optionally stores the
normalized application event/frame log. Reconstruction and analysis have separate error
boundaries, so a parser failure does not discard canonical JSON.

#### Content Categories (`analysis/classifier.py`)

`classify_blocks(input_blocks)` assigns each **input** block a `category`, in priority order
(first match wins):

1. `tool_result` block → `tool_results`
2. `system_prompt` block → `system_prompt`
3. `attrs["is_prefill"]` set → `assistant_prefill`
4. `user_message` block → `file_contents` (if `_is_file_content` matches), else
   `current_user_message` if it's the last non-prefill user turn, else `conversation_history`
5. `tool_definition` block → `tool_definitions`
6. `assistant_message` / `tool_call` / `thinking` / `other` → `file_contents` (if
   `_is_file_content` matches) else `conversation_history`
7. anything else → `uncategorized`

`classify(analyzed)` sums block `token_count`s per category into a `CategoryBreakdown`, adds a
small ChatML-style overhead estimate to `total_input`, and splits **output** blocks into
`tokens_output_thinking` (`thinking` blocks) vs. `tokens_output_text` (everything else), summed
into `total_output`. `per_tool_tokens(analyzed)` produces one row per `tool_definition` block
(`tool_name`, `definition_tokens`, `result_tokens`), attributing each `tool_result` block's tokens
back to its tool via `tool_call_map`; unattributable result tokens are evenly split across rows.

#### File Content Detection Heuristics

A block's content is classified as `file_contents` if **any** of the following match:

1. Contains XML-like tags: `<file_contents>`, `<file>`, `<source>`, `<document_content>` (common in Anthropic prompt patterns).
2. Contains a fenced code block (``` or ~~~) with a filename hint on the opening fence (e.g., ` ```typescript src/foo.ts `).
3. Contains a large fenced code block with ≥ 50 lines of content.
4. Contains lines starting with a comment that looks like a filename path:  
   - `// relative/path/to/file.ext` or `# relative/path/to/file.ext`  
   - followed immediately by code.
5. Content starts with a path-like string (contains `/` or `\` and a file extension) on its own line, followed by a code block.

These heuristics are intentionally broad. False positives are acceptable; the goal is useful approximation, not perfect categorisation.

#### Block Linking

Blocks are linked to related blocks **at read time** (in `db/crud.py: get_blocks()`), not via a
stored foreign key — the join keys (`tool_name`, `tool_call_id`, `message_index`) already exist
on every block, and both sides of each link always live in the same request's block set (the
agent resends full history each turn), so no extra migration is needed:

- `tool_call` blocks → `linked_definition_id` (matching `tool_definition` by `tool_name`).
- `tool_result` blocks → `linked_call_id` (matching `tool_call` by `tool_call_id`) and
  `linked_definition_id`.
- `user_message`/`assistant_message` blocks → `linked_previous_message_id`, chaining by
  `message_index` to the previous conversational turn, skipping over tool-only turns
  (calls/results) and the system prompt in between.

The request workbench's block inspector renders these relationships as jump actions that select
and scroll to the linked block. Each returned block also includes `first_seen_session_seq`, the
earliest request number in the same session where its content hash appeared.

---

### 5.3 Token Counter

**Library:** `tiktoken`  
**Encoder:** `o200k_base` (used as a universal approximation for all providers and models; `cl100k_base` up to 0.3.3).

**Expected accuracy:**
- OpenAI `gpt-5.x`, `gpt-4.1`, `gpt-4o`, o-series: typically within ~2%. `o200k_base` is their
  native encoder, while message/tool framing is still estimated locally.
- OpenAI `gpt-4` / `gpt-3.5-turbo`: ~2–5%. These predate `o200k_base` and use `cl100k_base`
  natively, but the two encoders agree to within ~0.0% on captured agent traffic (code, tool
  JSON, English prose).
- Anthropic Claude models: ~15–30% undercount — their tokenizer matches neither encoder and yields materially more tokens for the same text (measured 13–39% on recent `claude-haiku-4-5` turns against the provider's own `usage`). Always the low side, never high.
- Ollama models: ~10–20% error depending on model family (LLaMA, Mistral, Qwen, etc.).

All token count records include a `tokenizer` field set to `"tiktoken/o200k_base"` (rows written before 0.3.4 carry `"tiktoken/cl100k_base"`) so that counts made under different encoders stay distinguishable, and so future re-counting with native tokenizers is possible without schema changes.

**Proxy bypass during initialisation:**  
When the encoder is first loaded, tiktoken downloads the encoding data file from the internet. If proxy environment variables (`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, and their lowercase variants) are set — which they will be when ContextSpy routes traffic through itself — the download attempt is routed through the proxy, resulting in a `ProxyError`. To prevent this, `_get_encoder()` strips all proxy env vars from `os.environ` before calling `tiktoken.get_encoding()`, then restores them in a `finally` block. The encoder is cached globally so this only happens once per process.

**What is counted:**
- Each `Block`'s `token_count` is computed individually (`Block.make()` calls `tiktoken.encode()`
  on its content, unless a provider-reported count is passed explicitly — e.g. OpenAI Responses'
  hidden reasoning summaries, which have no visible content but a known token count).
- Inline image/audio/file transport payloads are represented by short typed markers rather than
  passing base64 data URLs to the text tokenizer. Mixed multimodal blocks carry
  `attrs.contains_media = true` and `attrs.token_estimate = "text_only"`; the provider-reported
  input total remains authoritative for media token accounting.
- The 8 category columns and output text/thinking split are sums over blocks. `tokens_total_input`
  additionally includes the ChatML-style message-overhead estimate; `tokens_total_output` is the
  output text/thinking sum. All are computed by `classify()` — see §5.2.
- If the provider returns a `usage` object in the response body, it's stored verbatim alongside
  the estimated counts: `provider_input_tokens`, `provider_output_tokens`,
  `provider_reasoning_tokens`, `cache_read_tokens`, `cache_creation_tokens`, and any remaining
  fields in `usage_extra` (JSON).

**Counting method:**  
Per-block token counts, not per-category concatenation — this is what makes block-level
persistence and per-tool/per-block drill-down possible.

---

### 5.4 Storage Layer

**Database:** SQLite at `~/.contextspy/contextspy.db`  
**ORM:** SQLAlchemy 2.0 (using `mapped_column` / `DeclarativeBase`)  
**Schema initialisation:** SQLAlchemy `create_all()` on startup creates any *missing tables*, but
does **not** add columns to existing tables — see "Schema Migrations" below for how column/data
changes are applied.

#### Schema

```sql
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,           -- UUID v4
    name        TEXT NOT NULL,
    started_at  DATETIME NOT NULL,
    ended_at    DATETIME,
    is_active   INTEGER NOT NULL DEFAULT 1  -- 1 = active, 0 = ended
);

CREATE TABLE requests (
    id                              TEXT PRIMARY KEY,   -- UUID v4
    session_id                      TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    timestamp                       DATETIME NOT NULL,
    provider                        TEXT NOT NULL,
        -- e.g. 'openai', 'openai_azure', 'anthropic', 'copilot',
        --      'opencode_zen', 'openai_chatgpt', 'ollama'
    model                           TEXT,
    agent                           TEXT,
        -- detected agent name or 'unknown'
    endpoint                        TEXT NOT NULL,      -- e.g. '/v1/chat/completions'
    duration_ms                     INTEGER,
    ttft_ms                         INTEGER,            -- time to first streamed token, if measurable
    status_code                     INTEGER,
    transport                       TEXT NOT NULL DEFAULT 'http',
    response_transport              TEXT NOT NULL DEFAULT 'legacy',
        -- 'json' | 'sse' | 'ndjson' | 'websocket' | 'text' | 'none' | 'legacy'
    response_reconstructed          INTEGER NOT NULL DEFAULT 0,
    response_complete               INTEGER NOT NULL DEFAULT 0,
    capture_error                   TEXT,               -- structured JSON warning/error
    provider_response_id            TEXT,
    predecessor_response_id         TEXT,
    invocation_outcome              TEXT NOT NULL DEFAULT 'unknown',
    context_fidelity                TEXT NOT NULL DEFAULT 'complete',
        -- 'complete' | 'partial' | 'opaque'
    context_notes                   TEXT,               -- JSON array

    -- Estimated token counts by category (aggregated from `blocks`, see below)
    tokens_system_prompt            INTEGER NOT NULL DEFAULT 0,
    tokens_tool_definitions         INTEGER NOT NULL DEFAULT 0,
    tokens_tool_results             INTEGER NOT NULL DEFAULT 0,
    tokens_file_contents            INTEGER NOT NULL DEFAULT 0,
    tokens_conversation_history     INTEGER NOT NULL DEFAULT 0,
    tokens_current_user_message     INTEGER NOT NULL DEFAULT 0,
    tokens_assistant_prefill        INTEGER NOT NULL DEFAULT 0,
    tokens_uncategorized            INTEGER NOT NULL DEFAULT 0,
    tokens_total_input              INTEGER NOT NULL DEFAULT 0,
    tokens_total_output             INTEGER NOT NULL DEFAULT 0,
    tokens_output_text              INTEGER NOT NULL DEFAULT 0,
    tokens_output_thinking          INTEGER NOT NULL DEFAULT 0,

    -- Provider-reported usage (from response body, may be NULL)
    provider_input_tokens           INTEGER,
    provider_output_tokens          INTEGER,
    provider_reasoning_tokens       INTEGER,
    cache_read_tokens               INTEGER,
    cache_creation_tokens           INTEGER,
    usage_extra                     TEXT,               -- JSON: leftover provider usage fields

    session_seq                     INTEGER,            -- this request's ordinal within its session

    tokenizer                       TEXT NOT NULL DEFAULT 'tiktoken/o200k_base',

    -- Raw content — purged per [retention] settings
    raw_request_body                TEXT,               -- complete decoded request payload
    canonical_request_body          TEXT,               -- exact JSON analyzed/displayed
    canonical_response_body         TEXT,               -- exact JSON analyzed/displayed
    raw_response_body               TEXT,               -- compatibility JSON/text fallback
    response_events                 TEXT                -- normalized SSE/NDJSON/WS events as JSON
);

CREATE INDEX idx_requests_session ON requests(session_id);
CREATE INDEX idx_requests_timestamp ON requests(timestamp);
CREATE INDEX idx_requests_provider ON requests(provider);
CREATE INDEX idx_requests_provider_response ON requests(provider, provider_response_id);
CREATE INDEX idx_requests_predecessor_response ON requests(predecessor_response_id);

CREATE TABLE tool_stats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    tool_name         TEXT NOT NULL,
    definition_tokens INTEGER NOT NULL DEFAULT 0,
    result_tokens     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_tool_stats_request ON tool_stats(request_id);
CREATE INDEX idx_tool_stats_name ON tool_stats(tool_name);

-- Content-addressed block text, shared/deduplicated globally by content hash
CREATE TABLE block_contents (
    hash        TEXT PRIMARY KEY,   -- sha256 hex of `content`
    content     TEXT NOT NULL,
    created_at  DATETIME NOT NULL
);

-- One row per content-part-level Block (system prompt, tool def, message, tool call/result,
-- thinking segment) produced by an adapter for a given request, on either direction.
CREATE TABLE blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    direction     TEXT NOT NULL,      -- 'input' | 'output'
    position      INTEGER NOT NULL DEFAULT 0,   -- order within (request_id, direction)
    message_index INTEGER,            -- conversational turn ordinal; NULL/-1 for turn-independent blocks
    block_type    TEXT NOT NULL,      -- see BlockType in §5.2
    category      TEXT,               -- classifier output; input blocks only
    content_hash  TEXT,               -- FK (by value, not enforced) → block_contents.hash
    token_count   INTEGER NOT NULL DEFAULT 0,
    tool_name     TEXT,
    tool_call_id  TEXT,
    attrs         TEXT                -- JSON, e.g. {"is_prefill": true}
);

CREATE INDEX idx_blocks_request ON blocks(request_id);
CREATE INDEX idx_blocks_content_hash ON blocks(content_hash);
CREATE INDEX idx_blocks_type ON blocks(block_type);

-- Tracks the schema/data-migration state (see "Schema Migrations" below)
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

`tool_call`/`tool_result`/previous-message links (`linked_call_id`, `linked_definition_id`,
`linked_previous_message_id`) are **not** stored columns — they're resolved at read time in
`db/crud.py: get_blocks()` from `tool_name`/`tool_call_id`/`message_index`. See §5.2 "Block
Linking".

#### Data Lifecycle

- **During capture:** every supported invocation writes a `Request` row with the aggregated per-category
  token counts, one `BlockRecord` per content part (deduplicated into `block_contents` by
  content hash across the database), and (if tool definitions exist) one `ToolStat` row per tool.
- **Retention (configurable, see §10):** on server startup only (no background timer),
  `startup_vacuum()`:
  - NULLs raw/canonical request/response bodies and `response_events` together on `Request` rows older than
    `retention.raw_body_days` (default 7; `0` = keep forever).
  - Deletes `block_contents` rows whose hash is no longer referenced by any `blocks` row from a
    request newer than `retention.block_content_days` (default 7; `0` = keep forever) — content
    shared by multiple requests anywhere in the database is only garbage-collected once every
    referencing request has aged out. `blocks` rows themselves (and their token counts/categories) are never
    purged, only the `block_contents` text.
- **Token stats, block metadata (types/categories/token counts/links), and tool stats** are kept
  indefinitely.

#### Schema Migrations

Because `create_all()` only creates missing tables, any change to `db/models.py` needs one or
both of:

1. **New column on an existing table** — added to the `new_columns` list in
   `db/database.py: _migrate()`, an additive `ALTER TABLE` applied automatically on every
   startup. No version bump needed for this alone.
2. **New derived/backfillable data** (e.g. a new column whose values must be computed from
   existing rows) — bumps `SCHEMA_VERSION` in `db/migrations.py` and registers a new
   `_migrate_to_vN` function in `_DATA_MIGRATIONS`. This does **not** run automatically; it only
   runs when the user explicitly invokes `contextspy db-upgrade`. `schema_meta` tracks the
   applied version and any pending migration IDs. `contextspy start`/`start-local` call
   `_abort_if_migrations_pending()` before booting and refuse to start (exit code 1, pointing the
   user at `db-upgrade` or `reset-db`) if any data migration is pending — this prevents the app
   from running against a DB with stale/missing derived data.

Currently `SCHEMA_VERSION = 4`; `_migrate_to_v2` backfills `session_seq` (per-session request
ordinal, assigned by `timestamp` order) and reconstructs `blocks`/`block_contents` rows for
pre-existing requests from their still-present `raw_request_body`/`raw_response_body` (re-running
the adapter → classify → insert_blocks pipeline). `_migrate_to_v3` copies retained provider JSON
into explicit canonical columns and reconstructs retained Responses WebSocket chains only through
exact provider response IDs, replacing derived blocks/tool stats transactionally when parsing
succeeds. Missing predecessors remain explicitly partial. `_migrate_to_v4` reanalyzes retained
requests containing inline base64 media so transport encodings are no longer counted as text.

---

### 5.5 REST API

**Framework:** FastAPI  
**Base URL:** `http://127.0.0.1:5173/api`  
**All responses:** `application/json`

#### Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/sessions` | Create and start a new session. Body: `{ "name": "string" }`. Returns session object. If another session is active, it is automatically ended first (warning included in response). |
| `GET` | `/api/sessions` | List all sessions (newest first). |
| `GET` | `/api/sessions/{id}` | Get session detail + aggregated token stats for that session. 404 if missing. |
| `PATCH` | `/api/sessions/{id}` | Rename a session. Body: `{ "name": "string" }`. 422 if blank, 404 if missing. |
| `POST` | `/api/sessions/{id}/end` | End a session. Retained content is unchanged until the next startup retention pass. 404 if missing. |
| `DELETE` | `/api/sessions/{id}?delete_requests=bool` | Delete session, optionally cascading its request records. 404 if missing. |

#### Requests

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/requests` | List requests (no raw bodies). Query params: `session_id`, `provider`, `agent`, `model`, `q` (text search), `status_category` (`success`\|`error`), `sort_by` (`timestamp`\|`tokens_total_input`\|`tokens_total_output`\|`duration_ms`\|`status_code`\|`session`\|`provider`\|`agent`\|`model`), `sort_dir`, `limit` (default 50, max 500), `offset` (default 0). |
| `GET` | `/api/requests/{id}` | Full transport-neutral request detail. `request_body`/`response_body` resolve to the stored canonical documents, with outcome, context fidelity/accounting, usage, and compatibility diagnostics when retained. Block data is fetched from the companion `/blocks` endpoint. 404 if missing. |
| `GET` | `/api/requests/{id}/blocks` | Structured block breakdown for one request: `{ "session_seq": int\|null, "blocks": [Block, ...] }`. Each `Block`: `id, direction, position, message_index, block_type, category, content, content_purged, token_count, tool_name, tool_call_id, attrs, linked_call_id, linked_definition_id, linked_previous_message_id, first_seen_session_seq`. `content` is `null` and `content_purged: true` if the backing `block_contents` row has been garbage-collected by retention. `first_seen_session_seq` is `null` for session-less or content-less blocks. 404 if request missing. |

#### Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats/overview` | Aggregated totals across all recorded requests. |
| `GET` | `/api/stats/session/{id}` | Aggregated breakdown for a specific session. |
| `GET` | `/api/stats/timeline` | Time-series data. Query params: `session_id` (optional), `bucket` = `minute` \| `hour` \| `day`. |
| `GET` | `/api/stats/tools` | Per-tool token breakdown (`tool_name`, `definition_tokens`, `result_tokens`). Query params: `session_id`, `request_id` (both optional; live-aggregated from `tool_stats`, not materialized separately). |
| `GET` | `/api/stats/sessions-summary` | Newest-first list of session and no-session gap entries, including duration, request/input/output totals, and all eight input-category totals. Used by the Overview and Sessions pages. |

Stats response shape (shared by overview and per-session):
```json
{
  "request_count": 42,
  "tokens_total_input": 128000,
  "tokens_total_output": 8200,
  "tokens_output_text": 6900,
  "tokens_output_thinking": 1300,
  "by_category": {
    "system_prompt":            { "tokens": 4000,  "pct": 3.1 },
    "tool_definitions":         { "tokens": 32000, "pct": 25.0 },
    "tool_results":             { "tokens": 18000, "pct": 14.1 },
    "file_contents":            { "tokens": 55000, "pct": 43.0 },
    "conversation_history":     { "tokens": 12000, "pct": 9.4 },
    "current_user_message":     { "tokens": 5000,  "pct": 3.9 },
    "assistant_prefill":        { "tokens": 0,     "pct": 0.0 },
    "uncategorized":            { "tokens": 2000,  "pct": 1.6 }
  },
  "by_provider": { "openai": 30, "anthropic": 12 },
  "by_agent":    { "github_copilot": 22, "claude_sdk": 12, "unknown": 8 },
  "by_model":    { "gpt-5.6": 30, "claude-sonnet-4-5": 12 },
  "latency": {
    "avg_ms": 1234, "p50_ms": 950, "p95_ms": 3100,
    "p99_ms": 4200, "min_ms": 180, "max_ms": 4800
  },
  "by_status": { "200": 40, "failed": 2 },
  "error_count": 2,
  "unknown_status_count": 0,
  "session_timing": {
    "first_request_at": "2026-09-10T08:00:00+00:00",
    "last_request_at": "2026-09-10T08:30:00+00:00",
    "elapsed_ms": 1800000,
    "active_duration_ms": 51828
  }
}
```

#### Proxy Control

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/proxy/status` | `{ "running": bool, "port": int, "cert_installed": bool }` |
| `POST` | `/api/proxy/start` | Start the proxy (no-op if already running). |
| `POST` | `/api/proxy/stop` | Stop the proxy. |
| `POST` | `/api/proxy/install-cert` | Install the mitmproxy CA cert into the OS trust store. |
| `GET` | `/api/proxy.pac` | PAC file (`text/plain`) routing known LLM hostnames through the proxy, `DIRECT` otherwise — an alternative to setting `HTTPS_PROXY` for clients that support PAC. |

#### Tokenize

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/tokenize` | Compatibility endpoint. Body: `{ "texts": string[] }`. Returns `{ "results": string[][] }` — per-text token strings. Processing is capped at 200 inputs and 50,000 characters per input. |
| `POST` | `/api/tokenize/window` | Body: `{ "text": string, "offset": number }`, with the offset expressed in UTF-16 code units. Returns Unicode-safe token display segments, absolute UTF-16 window bounds, truncation flags, total length, and tokenizer ID. The server caps each window at 50,000 characters and 8,000 tokens. |

#### WebSocket

`WS /api/ws`

The server pushes JSON messages to all connected clients when a new request is captured:

```json
{
  "event": "new_request",
  "data": { /* full request record, without raw bodies */ }
}
```

Also emits `{ "event": "session_started", "data": { ... } }` and `{ "event": "session_ended", "data": { ... } }`.

---

### 5.6 Web UI

**Stack:**
- React 18 + TypeScript
- Vite (dev server + production build)
- TanStack Query v5 (data fetching + cache)
- Recharts (charts)
- Tailwind CSS (styling)
- React Router v6 (routing)
- jsPDF + jsPDF-AutoTable (session PDF export)

Vite writes the production build directly to `contextspy/_web/`, which FastAPI serves as static
files with an SPA fallback. During development, the Vite server on port `5174` proxies `/api`
(including `/api/ws`) to FastAPI on port `5173`.

The application uses semantic CSS variables for all surfaces, text, state, chart, and block
colours. Light and dark themes follow a saved `contextspy-theme` preference, falling back to the
OS colour scheme; the theme is applied before React loads to avoid a flash. The application shell
uses a full sidebar on wide screens, an icon rail at narrower desktop widths, and a sticky header
plus navigation drawer on mobile.

#### Pages

##### `/` — Dashboard

- Header-level session controls show the active session or open the **Start session** dialog; an
  active session can be ended in place.
- Global summary cards: context tokens, generated tokens (split into visible output and thinking
  when applicable), total requests, and provider count.
- Responsive token-composition donut with an adjacent exact-value category table.
- Paginated five-row session/no-session timeline summary.
- Tool composition treemap plus a sortable exact-value table. Treemap area is token-linear,
  definition/result shades are related, colours are stable by tool name, and tools below 1% of
  tool tokens are grouped into **Other**.
- Top-ten model distribution and latency (`avg`, `p50`, `p95`, `p99`) / error summaries.
- Recent 20 requests using the shared responsive request list.

##### `/requests` — All Requests

- Search across model, endpoint, agent, and provider; dynamically populated provider and agent
  filters; and success/error status filtering. The UI also reports how many models have been seen.
- Server-backed pagination at 50 rows per page and sortable primary columns.
- The shared request list hides zero-input/zero-output rows by default (toggleable). Its desktop
  table shows time, input tokens, a compact category bar, duration, status, and model/source;
  output/thinking, provider, agent, session, and endpoint are available in an expandable detail
  row. Mobile uses request cards. Selecting a row opens Request Detail.

##### `/requests/:id` — Request Detail

- Compact header with status/provider/model/time plus context, generated, duration, model, and
  cache summary cards. Capture/fidelity/reconstruction warnings appear directly below it.
- The request-composition workbench is open by default. Top-level **Request** / **Response**
  direction controls share three views:
  - **Compact:** one equal-size, ordered tile per visible block.
  - **Proportional:** an ordered, wrapped relative-size view. Each whole block spans one to six
    cells using logarithmic token-count growth; blocks are not split across rows. This keeps very
    large blocks usable, so the view is intentionally not an exact area chart.
  - **Raw:** the canonical request/response payload, with the normalized event log available for
    streamed responses.
- Block controls include content/tool search, type filters, three tile-size choices, a zero-token
  visibility toggle, and **Jump to largest**. Compact view also marks sequence/turn/tool grouping;
  Proportional view preserves sequence without group separators. Zero-token blocks remain visible
  by default with muted category colours.
- Selecting a block opens an inspector with type, tokens, position, message, first-seen request,
  content state, tool-call ID, and jumpable tool/previous-message relationships. Available block
  content appears in a bounded viewer with JSON pretty-printing, search with next/previous match,
  and copy support. Purged and structural empty content have distinct states.
- Collapsible **Analytics** contains request-level category composition and tool treemap/table.
  Collapsible **Metadata and diagnostics** contains transport, provider usage, context-accounting,
  lineage, cache, tokenizer, and additional usage fields.

##### `/sessions` — Sessions

- Sortable table of named sessions with start, duration, active/ended status, request count, input
  and output totals, and an eight-category context bar.
- Sessions can be renamed inline or deleted. The delete dialog can either keep requests as
  session-less records or delete the session's requests too. Selecting a row opens Session Detail.

##### `/sessions/:id` — Session Detail

- Session timing (opened/closed, first/last request, elapsed and active request duration), totals,
  token-composition donut/table, selectable minute/hour/day timeline, tool treemap/table, and up
  to 500 requests using the shared sortable request list.
- Actions: **End session** (when active), **Export PDF**, **Rename**, and **Delete**. The PDF
  contains timing, totals, category and tool tables, and up to the same 500 request rows; it notes
  when the list is truncated relative to the full session summary.

##### `/settings` — Settings

- **Proxy** tab: CA certificate status, one-click installation, and the returned installation
  message. It does not edit the listening port or configuration file.
- **Agent setup** tab: cloud forward-proxy instructions for Claude Code/Anthropic, GitHub Copilot,
  and opencode, plus reverse-proxy configuration examples for llama-server, Ollama, and vLLM.

---

### 5.7 CLI

**Entry point:** `contextspy` (installed by `pip install -e .` via `pyproject.toml` `[project.scripts]`).
**CLI framework:** Typer.

```
contextspy start [--proxy-port 8888] [--web-port 5173] [--no-browser]
    Start both the forward proxy and web server (cloud mode).
    Opens browser to http://127.0.0.1:5173 on startup.
    Ctrl+C for clean shutdown.

contextspy start-local [--web-port 5173] [--no-browser]
    Start reverse-proxy listeners for local LLM servers + web server.
    Reads [[reverse_targets]] from ~/.contextspy/config.toml.
    No CA certificate required.
    Ctrl+C for clean shutdown.

contextspy run <tool> [args...]
    Run a command with the proxy env vars (and, for known tools, the right
    cert variable) pre-set, so you don't need to export them manually.
    Requires contextspy to already be running; aborts if the CA cert is
    missing for tools that need it.

contextspy help
    Print a table of all available commands with descriptions.

contextspy status
    Show whether the forward proxy and CA certificate are available, the reported
    proxy port, and the active session name.

contextspy install-cert
    Run OS-specific CA cert trust-store installation.

contextspy reset-db [--yes]
    Delete ALL rows from tool_stats, blocks, block_contents, requests,
    sessions, and schema_meta (in that order; missing tables are ignored,
    for compatibility with older DBs). Prompts for confirmation unless
    --yes is passed.

contextspy db-upgrade
    Apply any pending data migrations (see §5.4 "Schema Migrations").
    Before initialization or migration changes the database, copy the original
    SQLite file beside it as
    <db_name>_backup_v<version_from>_to_v<version_to>_<YYYY-MM-DD-HHMM>.back (UTC),
    adding -1, -2, and so on before .back if the name already exists.
    Prints "already up to date" if nothing is pending. `start`/`start-local`
    refuse to boot until this (or reset-db) has been run against a DB with
    pending migrations.

contextspy db-stats
    Print row counts for each table in the database (offline — no server needed).

contextspy report
    Print aggregate stats: total requests, input/output tokens (estimated and
    provider-reported), an input token category breakdown table with
    percentages and a bar indicator, and a per-tool token breakdown table.

contextspy setup-claude
    Print the exact PowerShell and Bash env-var commands needed to route
    Claude Code traffic through the proxy (HTTPS_PROXY + NODE_EXTRA_CA_CERTS).

contextspy setup-copilot
    Print the exact PowerShell, Bash, and VS Code settings.json snippet
    needed to route GitHub Copilot traffic through the proxy.

contextspy setup-opencode
    Print env-var commands to route opencode through the proxy.

contextspy setup-codex
    Print env-var commands for Codex CLI. Documents native capture of the
    ChatGPT-plan WebSocket transport and the now-unnecessary HTTP workaround.

contextspy setup-python
    Print httpx/OpenAI-SDK cert setup instructions, including the fix for
    SDKs that verify against certifi's bundled CA store directly and so
    ignore SSL_CERT_FILE/REQUESTS_CA_BUNDLE.

contextspy inject-cert
    Append the mitmproxy CA cert into certifi's bundled CA store (the
    one-shot fix referenced by setup-python).

contextspy setup-llamaserver
    Print config.toml snippet and client base-URL change for llama.cpp / llama-server.

contextspy setup-ollama
    Print config.toml snippet and client base-URL change for Ollama.

contextspy setup-vllm
    Print config.toml snippet and client base-URL change for vLLM.

contextspy session start <name>
    Start a named session (calls POST /api/sessions).
    Ends any currently active session first.

contextspy session end
    End the active session (calls POST /api/sessions/{active_id}/end).

contextspy session list
    Print a table of sessions.

contextspy --version
    Print the installed package version and exit.
```

`session start`, `session end`, `session list`, and `status` require the web server to be running (they call the REST API on localhost). `reset-db`, `db-upgrade`, `db-stats`, `report`, and all `setup-*`/`inject-cert` commands work offline.

---

## 6. Provider & Agent Identification

### Provider Detection

Determined from the destination hostname/port of the intercepted flow. Host patterns match the
exact hostname and all subdomains:

| Hostname | Provider value |
|---|---|
| `api.openai.com` | `openai` |
| `openai.azure.com` and subdomains | `openai_azure` |
| `api.anthropic.com` | `anthropic` |
| `copilot-proxy.githubusercontent.com` | `copilot` |
| `githubcopilot.com` and subdomains | `copilot` |
| `opencode.ai` and subdomains | `opencode_zen` |
| `chatgpt.com` and subdomains | `openai_chatgpt` |
| any hostname on port `11434` | `ollama` |
| anything else | not captured |

A recognized host is not sufficient by itself: only requests parsed by an adapter, or requests
whose path resembles a supported LLM endpoint, are persisted. This is especially important for
broad hosts such as `chatgpt.com`, which also carry telemetry and account traffic.

### Agent Detection

Determined by partial case-insensitive matching against the `User-Agent` request header. For a
registered WebSocket connection, `User-Agent` and `originator` are combined before matching:

| User-Agent contains | Agent value |
|---|---|
| `GithubCopilot` or `github-copilot` | `github_copilot` |
| `anthropic-python` | `claude_sdk` |
| `openai-python` | `openai_sdk` |
| `opencode` | `opencode` |
| `cursor` | `cursor` |
| `codex-tui`, `codex desktop`, or `codex_cli_rs` | `codex` |
| `claude-code` or `claude-cli` | `claude_code` |
| no match | `unknown` |

**Drawbacks:** User-Agent is not guaranteed to be set, is not authenticated, and multiple tools using the same SDK will share the same `agent` label. The `agent` field is informational only; it does not affect analysis or storage logic.

### GitHub Copilot — Special Configuration

Copilot in VS Code may not honour the system `HTTPS_PROXY` environment variable because VS Code uses its own proxy layer. Add to VS Code `settings.json`:

```json
{
  "http.proxy": "http://127.0.0.1:8888",
  "http.proxyStrictSSL": false
}
```

> **Note:** `http.proxyStrictSSL: false` disables TLS verification for VS Code extensions. The mitmproxy CA cert should also be installed system-wide. This is a known limitation of intercepting Copilot traffic and is acceptable for a local development/experimentation tool.

---

## 7. Session Management

### Lifecycle

```
ContextSpy session start "feat/auth-refactor"
        │
        ▼
  INSERT sessions row  (is_active=1)
        │
        ▼
  All proxy captures → session_id = this session
  Observed and canonical request/response bodies stored in DB
        │
        ▼
ContextSpy session end   (or UI button)
        │
        ▼
  UPDATE sessions SET ended_at=now, is_active=0
        │
        ▼   (retention runs on next application startup)
  Raw/canonical bodies, event logs, and expired block content are purged
  according to [retention] settings
```

### Rules

- Only one session is active at a time.
- Starting a new session automatically ends the active one (with a warning message).
- Requests captured while no session is active have `session_id = NULL`.
- Sensitive payload retention applies consistently to session and session-less requests.

---

## 8. Project Structure

```
contextspy/                         # repo root
├── contextspy/                     # Python package
│   ├── __init__.py
│   ├── __main__.py                 # PyInstaller / python -m entry point
│   ├── cli.py                      # Typer CLI entry point
│   ├── config.py                   # Settings (ports, paths, etc.)
│   ├── normalization.py            # Provider-state lineage → standalone canonical invocation
│   ├── proxy/
│   │   ├── __init__.py
│   │   ├── addon.py                # mitmproxy ContextSpyAddon
│   │   ├── cert.py                 # CA cert generation & OS trust-store install
│   │   ├── runner.py               # Starts mitmproxy in background threads
│   │   └── ws_protocols/           # WebSocket exchange assemblers and registry
│   │       ├── base.py
│   │       └── codex.py
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── blocks.py               # Block / Direction / BlockType / Usage / AnalyzedRequest
│   │   ├── capture.py              # SSE/NDJSON event decoding and canonical response type
│   │   ├── invocations.py          # Canonical JSON documents and analysis boundary
│   │   ├── adapters/               # One WireFormatAdapter subclass per wire format
│   │   │   ├── __init__.py         # Registers adapters (dispatch priority order)
│   │   │   ├── base.py             # WireFormatAdapter ABC, REGISTRY, get_adapter()
│   │   │   ├── anthropic.py
│   │   │   ├── openai_chat.py
│   │   │   ├── openai_responses.py
│   │   │   └── ollama.py
│   │   ├── classifier.py           # classify_blocks / classify / per_tool_tokens
│   │   └── tokenizer.py            # tiktoken wrapper (with proxy bypass)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py               # SQLAlchemy ORM models (incl. BlockRecord, BlockContent, SchemaMeta)
│   │   ├── database.py             # Engine + session factory + additive column migration + startup_vacuum
│   │   ├── migrations.py           # SCHEMA_VERSION + data migrations (contextspy db-upgrade)
│   │   └── crud.py                 # Database read/write helpers (incl. block link resolution)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app factory
│   │   ├── websocket.py            # WebSocket manager
│   │   └── routers/
│   │       ├── sessions.py
│   │       ├── requests.py         # incl. GET /requests/{id}/blocks
│   │       ├── stats.py
│   │       ├── proxy.py
│   │       └── tokenize.py
│   └── _web/                       # Built React assets (gitignored, generated by Vite)
├── ui/                             # React frontend source
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/                    # TanStack Query hooks + fetch wrappers
│   │   ├── lib/                    # Block visuals/layout and content-search helpers
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Requests.tsx
│   │   │   ├── RequestDetail.tsx
│   │   │   ├── Sessions.tsx
│   │   │   ├── SessionDetail.tsx
│   │   │   └── Settings.tsx
│   │   └── components/
│   │       ├── Layout.tsx           # Responsive shell + theme toggle
│   │       ├── TokenDonut.tsx
│   │       ├── TimeSeriesChart.tsx
│   │       ├── RequestTable.tsx
│   │       ├── SessionControls.tsx
│   │       ├── ToolTreemap.tsx
│   │       ├── ToolBreakdown.tsx
│   │       ├── request/             # Workbench, maps, toolbar, inspector, notices
│   │       └── ui/                  # Shared primitives, content viewer, and theme control
│   ├── package.json
│   └── vite.config.ts              # outDir → ../contextspy/_web, dev port 5174
├── .github/
│   ├── workflows/
│   │   ├── publish.yml             # PyPI publish on v* tag
│   │   └── release-binary.yml      # PyInstaller binary build + Homebrew formula update
│   └── scripts/
│       └── update-formula.py       # Patches version + sha256 in homebrew-contextspy
├── brew-formula/
│   └── contextspy.rb               # Homebrew formula template (copied to homebrew-contextspy)
├── contextspy.spec                 # PyInstaller one-file build spec
├── pyproject.toml
├── MANIFEST.in
├── LICENSE                         # Apache 2.0
├── NOTICE
├── README.md
├── SPEC.md                         # This file
├── Makefile
└── uv.lock
```

---

## 9. Python Dependencies

`pyproject.toml`:

```toml
[project]
name = "contextspy"
version = "0.3.5"
requires-python = ">=3.11"
license = {file = "LICENSE"}
dependencies = [
    "mitmproxy>=10.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "sqlalchemy>=2.0",
    "tiktoken>=0.7",
    "typer>=0.12",
    "websockets>=12.0",
    "rich>=13.0",
    "httpx>=0.27",
    "tomli>=2.0; python_version < '3.11'",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
contextspy = "contextspy.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.package-data]
contextspy = ["_web/**/*"]
```

Frontend dependencies (`ui/package.json`):

- Runtime: `react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`, `recharts`,
  `jspdf`, and `jspdf-autotable`.
- Build/test: `typescript`, `vite`, `@vitejs/plugin-react`, Tailwind CSS 3 + PostCSS/Autoprefixer,
  Vitest, jsdom, React Testing Library, and `@testing-library/user-event`.

---

## 10. Configuration

Config file: `~/.contextspy/config.toml` (created on first run with defaults).

```toml
[proxy]
port = 8888
bind_addr = "127.0.0.1"

[web]
port = 5173
bind_addr = "127.0.0.1"

[storage]
db_path = "~/.contextspy/contextspy.db"

[retention]
# Raw request/response bodies and block content text are purged at server
# startup only (no background timer) once they're older than these many
# days. 0 = keep forever. Block/category/type metadata is never purged,
# only the underlying text.
raw_body_days = 7
block_content_days = 7

[intercepted_hosts]
# Reserved setting. It is parsed into Settings.extra_hosts but is not yet
# consulted by provider detection or PAC generation.
extra_hosts = []

# Each [[reverse_targets]] block defines one local LLM server to intercept
# in reverse-proxy mode (used by 'contextspy start-local').
# [[reverse_targets]]
# name        = "llama-server"            # display label
# listen_port = 8889                      # port contextspy listens on
# target_url  = "http://127.0.0.1:8080"  # where your server actually runs
# provider    = "openai"                  # stored label; path still selects the adapter
```

The `config.py` module loads this file and exposes a `Settings` object. `contextspy start` applies
its `--proxy-port` and `--web-port` values after loading the file (their defaults are `8888` and
`5173`); `start-local` applies `--web-port` and reads listener/upstream ports from
`[[reverse_targets]]`. Bind addresses, storage, retention, and reverse-target definitions remain
configuration-file values. `extra_hosts` is currently reserved and does not extend capture.

> **Windows note:** When writing the config file, `db_path` backslashes are converted to forward slashes before serialisation. Raw Windows paths (e.g. `C:\Users\...`) would cause `TOMLDecodeError` because TOML interprets `\U` and `\u` as Unicode escapes in double-quoted strings.

---

## 11. Startup Sequence

### 11.1 Cloud Mode (`contextspy start`)

When `contextspy start` is called:

1. Load config, apply the CLI port values, ensure directories, and create the default config file
   if it does not exist.
2. Initialise the SQLite schema/additive columns and check for pending data migrations. If any are
   pending, print an error pointing at `db-upgrade`/`reset-db` and exit before starting services.
3. Validate or generate the mitmproxy CA. A newly generated CA triggers one automatic trust-store
   installation attempt; an existing valid CA is reused without reinstalling it.
4. Create the FastAPI application and start Uvicorn on the configured web bind address/port. Unless
   `--no-browser` is set, schedule the dashboard to open after a short delay.
5. During the FastAPI lifespan startup, initialise the DB again, run the one-time retention vacuum,
   and start mitmproxy `DumpMaster` with `ContextSpyAddon` in a daemon thread.
6. On shutdown, stop/join the proxy thread and dispose the DB engine.

### 11.2 Local Mode (`contextspy start-local`)

When `contextspy start-local` is called:

1. Load config, apply the CLI web-port value, ensure directories, create defaults if needed, and run
   the same schema/pending-migration check as cloud mode.
2. Abort with a configuration example if `reverse_targets` is empty.
3. Skip CA generation/installation, create the local FastAPI application, and start Uvicorn. Unless
   `--no-browser` is set, schedule the dashboard to open after a short delay.
4. During FastAPI lifespan startup, initialise the DB again, run the one-time retention vacuum, and
   start one staggered daemon-thread `DumpMaster` per `[[reverse_targets]]` entry in `reverse:` mode
   with `ContextSpyAddon(provider_override=target.provider)`.
5. On shutdown, stop/join all reverse-proxy threads and dispose the DB engine.

---

## 12. Open Questions / Future Work

- **Native tokenizer support:** Anthropic provides a token-counting API endpoint; Ollama has `/api/tokenize`. These could be used for exact counts per provider.
- **Cost estimation:** Add a `models_pricing.json` lookup table (input/output price per 1K tokens per model) to compute estimated cost per request.
- **Additional export formats:** Session PDF export exists; CSV / JSON export is not yet built.
- **Prompt diffing:** Visual diff of the context window between consecutive requests in the same
  session. Groundwork laid: every `Request` has a `session_seq` ordinal and every `Block` a
  `content_hash`, so unchanged blocks across consecutive requests can already be identified by
  hash equality — the diffing UI/logic itself is not yet built.
- **opencode User-Agent:** Confirm the User-Agent string once opencode is available for testing.
- **Re-tokenisation:** Add an API endpoint to re-count tokens for historical requests using a different tokenizer, without re-capturing.

## 13. Known Issues & Resolved Bugs

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `TOMLDecodeError: Invalid hex value` on Windows | Windows path `C:\Users\...` written to TOML double-quoted string — `\U` is a TOML Unicode escape | Convert backslashes to forward slashes before writing |
| `RuntimeError: no running event loop` on proxy start | `DumpMaster.__init__` calls `asyncio.get_running_loop()` before the thread's loop is set | Pass `loop=loop` explicitly; construct `DumpMaster` inside the thread after `asyncio.set_event_loop(loop)` |
| `sys.exit(1)` killing uvicorn on port conflict | mitmproxy's built-in `ErrorCheck` addon calls `sys.exit` on any startup error | Remove `ErrorCheck` from `master.addons.chain` after construction |
| `is_running()` returning `True` when proxy is not bound | Only checked thread liveness | Added `_bound` flag set via `_BindWatcher` log handler |
| Hooks not firing for Claude Code requests | Claude Code uses SSE streaming; `response()` hook never fires for `text/event-stream` | Added `responseheaders()` hook + streaming callback that collects SSE chunks |
| `tiktoken` `ProxyError` on first run | tiktoken downloads the encoder data at first use; with `HTTPS_PROXY` set, the download is routed through the local proxy which can't handle it | `_get_encoder()` strips all proxy env vars from `os.environ` before calling `tiktoken.get_encoding()`, restores them in `finally` |
| macOS cert install: `SecCertificateCreateFromData: Unknown format in import` | `cert.py` Darwin branch passes `mitmproxy-ca.pem` (key + cert bundle) to `security add-trusted-cert`; macOS requires the cert-only file | **Fixed.** `cert.py` now uses `mitmproxy-ca-cert.pem` (cert-only PEM) on all platforms. |
| `contextspy db-upgrade` crashed with `OperationalError: no such column: requests.session_seq` on a pre-refactor DB | New `Request` columns (`tokens_output_text`, `tokens_output_thinking`, `provider_reasoning_tokens`, `usage_extra`, `session_seq`) were added to `db/models.py` but not to `db/database.py: _migrate()`'s `new_columns` list — `create_all()` only creates missing *tables*, not missing *columns* on existing ones | **Fixed.** Added the missing entries to `new_columns`. Rule going forward: every new/changed column on an existing table must be added there (see §5.4 "Schema Migrations"). |
