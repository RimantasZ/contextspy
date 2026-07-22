# ContextSpy — Technical Specification v0.3

> **Status:** Implemented — July 2026  
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
- Purge raw request/response bodies when a session ends, retaining only token stats and metadata.
- Display statistics and graphs in a browser UI served locally.
- Bind all network services to `127.0.0.1` only.

---

## 3. Non-Goals (v1)

- Production / multi-user deployment.
- Exact token counts via provider-native tokenizer APIs.
- Supporting providers beyond OpenAI, Anthropic, Ollama, and GitHub Copilot.
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
│  • Calls analysis pipeline on each response                  │
│  • Writes records to SQLite                                  │
└───────────────────────┬──────────────────────────────────────┘
                        │  forwards original HTTPS request
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  LLM Provider API                                            │
│  (api.openai.com, api.anthropic.com, localhost:11434, …)     │
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
│  • provider_override = "openai" (no hostname detection)      │
│  • Calls analysis pipeline on each response                  │
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
**Bind address:** `127.0.0.1` only.

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

Only intercept flows to the following hosts (all other traffic is passed through untouched):

| Hostname pattern | Provider |
|---|---|
| `api.openai.com` | `openai` |
| `*.openai.azure.com` | `openai_azure` |
| `api.anthropic.com` | `anthropic` |
| `copilot-proxy.githubusercontent.com` | `copilot` |
| `localhost` port `11434` | `ollama` |
| `127.0.0.1` port `11434` | `ollama` |

#### Addon Class: `ContextSpyAddon`

Accepts an optional `provider_override: str | None` constructor argument. When set, the addon skips hostname-based provider detection and always uses the specified provider string. Used by reverse-proxy mode where the upstream is a known local server.

The addon handles both regular JSON responses and SSE streaming responses:

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

    def responseheaders(self, flow):
        # For text/event-stream responses, attach a streaming callback
        # that collects SSE chunks. When the stream ends (empty bytes
        # sentinel), parse the accumulated SSE data and save the record.
        # Sets flow.metadata["is_sse"] = True to suppress response().

    def response(self, flow):
        # Skipped if is_sse is set (handled by stream callback).
        # Otherwise:
        # 1. Check hostname filter — skip if not an LLM host
        # 2. Extract request/response bodies (JSON)
        # 3. Detect provider from hostname
        # 4. Detect agent from User-Agent header
        # 5. Run analysis pipeline → get token breakdown
        # 6. Persist record to SQLite
        # 7. Emit WebSocket event for live UI update
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
**Provider detection:** Bypassed — `provider_override` is set from config, so the full OpenAI/Anthropic parser runs unconditionally.

Each reverse target is described by:

| Field | Type | Description |
|---|---|---|
| `name` | str | Human label (e.g. `"llama-server"`) |
| `listen_port` | int | Port contextspy binds (e.g. `8889`) |
| `target_url` | str | Upstream URL (e.g. `"http://127.0.0.1:8080"`) |
| `provider` | str | Parser to use: `"openai"` \| `"anthropic"` \| `"ollama"` |

All three local server types expose an OpenAI-compatible `/v1/chat/completions` endpoint, so `provider = "openai"` is the correct choice for llama-server, Ollama (`/v1` endpoint, requires Ollama ≥ 0.1.24), and vLLM.

`start_local_proxies(settings, ws_manager)` in `contextspy/proxy/runner.py` iterates over `settings.reverse_targets` and spawns one daemon thread per target. `stop_local_proxies()` shuts them all down cleanly.

---

### 5.2 Context Analyser

Runs synchronously after each captured response, entirely in the backend (the frontend does no
JSON parsing — it only renders what the backend already classified and persisted). The pipeline
is provider-agnostic: a **wire-format adapter** turns provider-specific JSON into a small set of
domain types, and a single classifier operates on those types regardless of which provider or
wire format produced them.

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
`parse_response(resp_body) -> (output_blocks, usage)` (buffered/non-streaming), and
`parse_sse(raw_bytes) -> (output_blocks, usage)` (streaming). `get_adapter(endpoint)` dispatches
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

Requests to unrecognised endpoints (`get_adapter` returns `None`) are recorded with
`tokens_uncategorized = tokens_total_input` and no `Block` rows.

#### SSE Streaming

Because Claude Code and most modern LLM clients use streaming responses
(`Content-Type: text/event-stream`), the `response()` mitmproxy hook never fires for these flows.
Instead `responseheaders()` detects `text/event-stream` and attaches a streaming callback that
accumulates raw SSE bytes; on stream end, `proxy/addon.py`'s `_handle_sse_response` decompresses
the body, resolves the adapter via `get_adapter(endpoint)`, and calls `adapter.parse_sse(raw)`
instead of `parse_response()`. A clean JSON response body is synthesized from the resulting
`AnalyzedRequest.response_text`/`usage` for storage. Everything downstream (classification,
persistence) is identical to the non-streaming path.

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

The UI (`ContextOverview`, `ParsedViewer`'s `TokenBlock`) renders these as clickable "jump to"
chips that scroll to and highlight the linked block.

---

### 5.3 Token Counter

**Library:** `tiktoken`  
**Encoder:** `cl100k_base` (used as a universal approximation for all providers and models).

**Expected accuracy:**
- OpenAI models (`gpt-4`, `gpt-4o`, `o1`, etc.): exact (this is the native encoder).
- Anthropic Claude models: ~5–15% error for English code/prose; up to ~20% for heavy tool JSON or non-English content.
- Ollama models: ~10–20% error depending on model family (LLaMA, Mistral, Qwen, etc.).

All token count records include a `tokenizer` field set to `"tiktoken/cl100k_base"` so that future re-counting with native tokenizers is possible without schema changes.

**Proxy bypass during initialisation:**  
When the `cl100k_base` encoder is first loaded, tiktoken downloads the encoding data file from the internet. If proxy environment variables (`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, and their lowercase variants) are set — which they will be when ContextSpy routes traffic through itself — the download attempt is routed through the proxy, resulting in a `ProxyError`. To prevent this, `_get_encoder()` strips all proxy env vars from `os.environ` before calling `tiktoken.get_encoding()`, then restores them in a `finally` block. The encoder is cached globally so this only happens once per process.

**What is counted:**
- Each `Block`'s `token_count` is computed individually (`Block.make()` calls `tiktoken.encode()`
  on its content, unless a provider-reported count is passed explicitly — e.g. OpenAI Responses'
  hidden reasoning summaries, which have no visible content but a known token count).
- `tokens_total_input`/`tokens_total_output` and the 8 category columns are sums over blocks,
  computed by `classify()` — see §5.2.
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
        -- 'openai' | 'openai_azure' | 'anthropic' | 'copilot' | 'ollama' | 'unknown'
    model                           TEXT,
    agent                           TEXT,
        -- detected agent name or 'unknown'
    endpoint                        TEXT NOT NULL,      -- e.g. '/v1/chat/completions'
    duration_ms                     INTEGER,
    ttft_ms                         INTEGER,            -- time to first streamed token, if measurable
    status_code                     INTEGER,

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

    tokenizer                       TEXT NOT NULL DEFAULT 'tiktoken/cl100k_base',

    -- Raw content — purged per [retention] settings
    raw_request_body                TEXT,
    raw_response_body               TEXT
);

CREATE INDEX idx_requests_session ON requests(session_id);
CREATE INDEX idx_requests_timestamp ON requests(timestamp);
CREATE INDEX idx_requests_provider ON requests(provider);

CREATE TABLE tool_stats (
    id                TEXT PRIMARY KEY,
    request_id        TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    tool_name         TEXT NOT NULL,
    definition_tokens INTEGER NOT NULL DEFAULT 0,
    result_tokens     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_tool_stats_request ON tool_stats(request_id);
CREATE INDEX idx_tool_stats_name ON tool_stats(tool_name);

-- Content-addressed block text, shared/deduplicated across every request in a session
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

- **During capture:** every request writes a `Request` row with the aggregated per-category
  token counts, one `BlockRecord` per content part (deduplicated into `block_contents` by
  content hash), and (if any tools were used) one `ToolStat` row per tool.
- **Retention (configurable, see §10):** on server startup only (no background timer),
  `startup_vacuum()`:
  - NULLs `raw_request_body`/`raw_response_body` on `Request` rows older than
    `retention.raw_body_days` (default 7; `0` = keep forever).
  - Deletes `block_contents` rows whose hash is no longer referenced by any `blocks` row from a
    request newer than `retention.block_content_days` (default 7; `0` = keep forever) — content
    shared by multiple requests in a session is only garbage-collected once every referencing
    request has aged out. `blocks` rows themselves (and their token counts/categories) are never
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

Currently `SCHEMA_VERSION = 2`; `_migrate_to_v2` backfills `session_seq` (per-session request
ordinal, assigned by `timestamp` order) and reconstructs `blocks`/`block_contents` rows for
pre-existing requests from their still-present `raw_request_body`/`raw_response_body` (re-running
the adapter → classify → insert_blocks pipeline), skipping requests whose raw bodies have already
been purged by retention.

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
| `POST` | `/api/sessions/{id}/end` | End a session. Triggers async raw content purge. 404 if missing. |
| `DELETE` | `/api/sessions/{id}?delete_requests=bool` | Delete session, optionally cascading its request records. 404 if missing. |

#### Requests

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/requests` | List requests (no raw bodies). Query params: `session_id`, `provider`, `agent`, `model`, `q` (text search), `status_category` (`success`\|`error`), `sort_by` (`timestamp`\|`tokens_total_input`\|`tokens_total_output`\|`duration_ms`\|`status_code`\|`session`\|`provider`\|`agent`\|`model`), `sort_dir`, `limit` (default 50, max 500), `offset` (default 0). |
| `GET` | `/api/requests/{id}` | Full request detail including raw bodies (if not yet purged). 404 if missing. |
| `GET` | `/api/requests/{id}/blocks` | Structured block breakdown for one request: `{ "session_seq": int\|null, "blocks": [Block, ...] }`. Each `Block`: `id, direction, position, message_index, block_type, category, content, content_purged, token_count, tool_name, tool_call_id, attrs, linked_call_id, linked_definition_id, linked_previous_message_id`. `content` is `null` and `content_purged: true` if the backing `block_contents` row has been garbage-collected by retention. 404 if request missing. |

#### Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats/overview` | Aggregated totals across all recorded requests. |
| `GET` | `/api/stats/session/{id}` | Aggregated breakdown for a specific session. |
| `GET` | `/api/stats/timeline` | Time-series data. Query params: `session_id` (optional), `bucket` = `minute` \| `hour` \| `day`. |
| `GET` | `/api/stats/tools` | Per-tool token breakdown (`tool_name`, `definition_tokens`, `result_tokens`). Query params: `session_id`, `request_id` (both optional; live-aggregated from `tool_stats`, not materialized separately). |
| `GET` | `/api/stats/sessions-summary` | Chronological list of session/gap entries (each session's token totals + idle gaps between sessions), used by the Sessions page. |

Stats response shape (shared by overview and per-session):
```json
{
  "request_count": 42,
  "tokens_total_input": 128000,
  "tokens_total_output": 8200,
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
  "by_agent":    { "github_copilot": 22, "claude_sdk": 12, "unknown": 8 }
}
```

#### Proxy Control

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/proxy/status` | `{ "running": bool, "port": 8888, "cert_installed": bool }` |
| `POST` | `/api/proxy/start` | Start the proxy (no-op if already running). |
| `POST` | `/api/proxy/stop` | Stop the proxy. |
| `POST` | `/api/proxy/install-cert` | Install the mitmproxy CA cert into the OS trust store. |
| `GET` | `/proxy.pac` | PAC file (`text/plain`) routing known LLM hostnames through the proxy, `DIRECT` otherwise — an alternative to setting `HTTPS_PROXY` for clients that support PAC. |

#### Tokenize

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/tokenize` | Body: `{ "texts": string[] }`. Returns `{ "results": string[][] }` — per-text token strings, used by the UI for token-level highlighting. |

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

The production build (`ui/dist/`) is served as static files by FastAPI. During development, Vite dev server proxies API requests to FastAPI.

#### Pages

##### `/` — Dashboard

- Header bar: active session name (or "No active session"), **Start Session** button (opens name input modal), **End Session** button (disabled if no active session).
- Summary cards: total requests, total input tokens this session, total output tokens this session, estimated cost placeholder (N/A in v1).
- **Donut chart** (Recharts `PieChart`): token composition for the current session, one slice per category, colour-coded.
- **Time-series line chart** (Recharts `LineChart`): total input tokens over time (bucketed). Controls for bucket size (minute / hour / day).
- **Recent requests table**: last 20 requests. Columns: timestamp, provider badge, model, agent, input tokens, output tokens, duration. Click row → Request Detail.

##### `/requests` — All Requests

- Filter bar: session selector, provider filter, agent filter.
- Paginated table (50 rows/page). Same columns as dashboard recent table.
- Click row → Request Detail.

##### `/requests/:id` — Request Detail

- Breadcrumb back to Requests list.
- **Token composition donut** scoped to this single request.
- **Category breakdown table**: category name, token count, % of total input.
- **Estimated vs. provider-reported** comparison table (shown only if `provider_input_tokens` is populated): estimated total, provider-reported total, difference, % error.
- **Raw request/response viewer**: two collapsible JSON panels, displayed only when raw content is available. Shows a notice if data has been purged.

##### `/sessions` — Sessions

- Table of all sessions: name, started, ended (or "Active"), request count, total input tokens.
- Click row → Session Detail.

##### `/sessions/:id` — Session Detail

- Same donut + time-series charts scoped to session.
- Full requests table for that session.
- Buttons: **End Session** (if active), **Delete Session** (with confirmation dialog).

##### `/settings` — Settings

- **Proxy configuration**: port (editable, requires restart).
- **CA Certificate**: installation status badge (installed / not installed), one-click install button (calls backend which runs OS command), manual instructions panel.
- **Agent setup instructions**: tabbed panel per agent, showing exact env vars / VS Code settings to configure.
  - *All agents (general):* `export HTTPS_PROXY=http://127.0.0.1:8888`
  - *GitHub Copilot:* VS Code `settings.json` snippet.
  - *Claude CLI / opencode:* env var instructions.
  - *OpenAI SDK scripts:* env var instructions.

---

### 5.7 CLI

**Entry point:** `ContextSpy` (installed by `pip install -e .` via `pyproject.toml` `[project.scripts]`).  
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
    Show whether the proxy is running, active session name, DB path.

contextspy install-cert
    Run OS-specific CA cert trust-store installation.

contextspy reset-db [--yes]
    Delete ALL rows from tool_stats, blocks, block_contents, requests,
    sessions, and schema_meta (in that order; missing tables are ignored,
    for compatibility with older DBs). Prompts for confirmation unless
    --yes is passed.

contextspy db-upgrade
    Apply any pending data migrations (see §5.4 "Schema Migrations").
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
```

`session start`, `session end`, `session list`, and `status` require the web server to be running (they call the REST API on localhost). `reset-db`, `db-upgrade`, `db-stats`, `report`, and all `setup-*`/`inject-cert` commands work offline.

---

## 6. Provider & Agent Identification

### Provider Detection

Determined from the destination hostname of the intercepted flow:

| Hostname | Provider value |
|---|---|
| `api.openai.com` | `openai` |
| `*.openai.azure.com` | `openai_azure` |
| `api.anthropic.com` | `anthropic` |
| `copilot-proxy.githubusercontent.com` | `copilot` |
| `localhost:11434` or `127.0.0.1:11434` | `ollama` |
| anything else (should not occur due to filter) | `unknown` |

### Agent Detection

Determined by partial case-insensitive matching against the `User-Agent` request header:

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
  Raw request/response bodies stored in DB
        │
        ▼
ContextSpy session end   (or UI button)
        │
        ▼
  UPDATE sessions SET ended_at=now, is_active=0
        │
        ▼   (background task)
  UPDATE requests SET raw_request_body=NULL, raw_response_body=NULL
  WHERE session_id = this session
```

### Rules

- Only one session is active at a time.
- Starting a new session automatically ends the active one (with a warning message).
- Requests captured while no session is active have `session_id = NULL`.
- Raw bodies for session-less requests older than 24 hours are vacuumed on startup.

---

## 8. Project Structure

```
contextspy/                         # repo root
├── contextspy/                     # Python package
│   ├── __init__.py
│   ├── __main__.py                 # PyInstaller / python -m entry point
│   ├── cli.py                      # Typer CLI entry point
│   ├── config.py                   # Settings (ports, paths, etc.)
│   ├── proxy/
│   │   ├── __init__.py
│   │   ├── addon.py                # mitmproxy ContextSpyAddon
│   │   ├── cert.py                 # CA cert generation & OS trust-store install
│   │   └── runner.py               # Starts mitmproxy in a background thread
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── blocks.py               # Block / Direction / BlockType / Usage / AnalyzedRequest
│   │   ├── adapters/                # One WireFormatAdapter subclass per wire format
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
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Requests.tsx
│   │   │   ├── RequestDetail.tsx
│   │   │   ├── Sessions.tsx
│   │   │   ├── SessionDetail.tsx
│   │   │   └── Settings.tsx
│   │   └── components/
│   │       ├── TokenDonut.tsx
│   │       ├── TimeSeriesChart.tsx
│   │       ├── RequestTable.tsx
│   │       ├── SessionControls.tsx
│   │       └── RawViewer.tsx
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
version = "0.3.0"
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
]

[project.scripts]
contextspy = "contextspy.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.package-data]
contextspy = ["_web/**/*"]
```

Frontend dependencies (`ui/package.json`):
- `react`, `react-dom`, `react-router-dom`
- `@tanstack/react-query`
- `recharts`
- `tailwindcss`, `@tailwindcss/vite`
- `typescript`, `vite`

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
# Add extra hosts if needed (besides the built-in list)
extra_hosts = []

# Each [[reverse_targets]] block defines one local LLM server to intercept
# in reverse-proxy mode (used by 'contextspy start-local').
# [[reverse_targets]]
# name        = "llama-server"            # display label
# listen_port = 8889                      # port contextspy listens on
# target_url  = "http://127.0.0.1:8080"  # where your server actually runs
# provider    = "openai"                  # parser: "openai" | "anthropic" | "ollama"
```

Config values can be overridden by CLI flags. The `config.py` module loads this file and exposes a `Settings` object.

> **Windows note:** When writing the config file, `db_path` backslashes are converted to forward slashes before serialisation. Raw Windows paths (e.g. `C:\Users\...`) would cause `TOMLDecodeError` because TOML interprets `\U` and `\u` as Unicode escapes in double-quoted strings.

---

## 11. Startup Sequence

### 11.1 Cloud Mode (`contextspy start`)

When `contextspy start` is called:

1. Load and validate config.
2. Ensure `~/.contextspy/` directory exists.
3. Initialise SQLite DB (create tables if not exists, apply additive column migrations, run startup vacuum).
4. Check for pending data migrations (`_abort_if_migrations_pending`); if any are pending, print an error pointing at `db-upgrade`/`reset-db` and exit(1) without starting anything else.
5. Check CA cert; if absent, generate via mitmproxy and attempt trust-store installation. If install fails, print instructions.
6. Start mitmproxy `DumpMaster` with `ContextSpyAddon` (no `provider_override`) in a daemon thread.
7. Start FastAPI/Uvicorn in the main asyncio event loop on `127.0.0.1:5173`.
8. Open `http://127.0.0.1:5173` in the default browser.
9. On `Ctrl+C`: send shutdown signal to mitmproxy thread, wait for it to stop, close DB connections, exit.

### 11.2 Local Mode (`contextspy start-local`)

When `contextspy start-local` is called:

1. Load and validate config; abort if `reverse_targets` is empty (print helpful config snippet).
2. Ensure `~/.contextspy/` directory exists.
3. Initialise SQLite DB (create tables if not exists, apply additive column migrations, run startup vacuum).
4. Check for pending data migrations (`_abort_if_migrations_pending`); exit(1) with the same message as cloud mode if any are pending.
5. **Skip CA cert check** — no TLS interception needed.
6. For each `[[reverse_targets]]` entry: start a mitmproxy `DumpMaster` in `reverse:` mode with `ContextSpyAddon(provider_override=target.provider)` in a daemon thread.
7. Start FastAPI/Uvicorn in the main asyncio event loop on `127.0.0.1:5173`.
8. Open `http://127.0.0.1:5173` in the default browser.
9. On `Ctrl+C`: send shutdown signal to all reverse-proxy threads, wait for them to stop, close DB connections, exit.

---

## 12. Open Questions / Future Work

- **Native tokenizer support:** Anthropic provides a token-counting API endpoint; Ollama has `/api/tokenize`. These could be used for exact counts per provider.
- **Cost estimation:** Add a `models_pricing.json` lookup table (input/output price per 1K tokens per model) to compute estimated cost per request.
- **Export:** CSV / JSON export of session data from the UI.
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
| `tiktoken` `ProxyError` on first run | tiktoken downloads `cl100k_base` data at first use; with `HTTPS_PROXY` set, the download is routed through the local proxy which can't handle it | `_get_encoder()` strips all proxy env vars from `os.environ` before calling `tiktoken.get_encoding()`, restores them in `finally` |
| macOS cert install: `SecCertificateCreateFromData: Unknown format in import` | `cert.py` Darwin branch passes `mitmproxy-ca.pem` (key + cert bundle) to `security add-trusted-cert`; macOS requires the cert-only file | **Fixed.** `cert.py` now uses `mitmproxy-ca-cert.pem` (cert-only PEM) on all platforms. |
| `contextspy db-upgrade` crashed with `OperationalError: no such column: requests.session_seq` on a pre-refactor DB | New `Request` columns (`tokens_output_text`, `tokens_output_thinking`, `provider_reasoning_tokens`, `usage_extra`, `session_seq`) were added to `db/models.py` but not to `db/database.py: _migrate()`'s `new_columns` list — `create_all()` only creates missing *tables*, not missing *columns* on existing ones | **Fixed.** Added the missing entries to `new_columns`. Rule going forward: every new/changed column on an existing table must be added there (see §5.4 "Schema Migrations"). |
