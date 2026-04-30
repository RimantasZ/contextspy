# Token-Scrooge — Technical Specification v0.1

> **Status:** Draft — April 30, 2026  
> **Purpose:** Feed to a coding agent to implement the full application.

---

## 1. Overview

Token-Scrooge is a local HTTPS proxy that sits between LLM coding agents (GitHub Copilot, Claude, opencode, OpenAI SDK clients) and their provider APIs. It captures every LLM request, analyses the composition of the context window, counts tokens per category, persists statistics in a local SQLite database, and serves a React web dashboard with charts and per-request drill-downs.

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
- Streaming response (SSE / `text/event-stream`) deep analysis — capture totals only from the final `usage` field.

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Coding Agent (Copilot / Claude / opencode / openai SDK)     │
└───────────────────────┬──────────────────────────────────────┘
                        │  HTTPS
                        │  via HTTPS_PROXY=http://127.0.0.1:8080
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  Token-Scrooge Proxy  (mitmproxy, 127.0.0.1:8080)                 │
│  • TLS termination via local CA cert                          │
│  • Filters to known LLM hostnames only                        │
│  • Calls analysis pipeline on each response                   │
│  • Writes records to SQLite                                   │
└───────────────────────┬──────────────────────────────────────┘
                        │  forwards original HTTPS request
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  LLM Provider API                                             │
│  (api.openai.com, api.anthropic.com, localhost:11434, …)      │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  Token-Scrooge Web Server  (FastAPI + Uvicorn, 127.0.0.1:5173)    │
│  • REST API for sessions, requests, stats, proxy control      │
│  • WebSocket endpoint for live updates                        │
│  • Serves built React UI as static files                      │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  SQLite Database  (~/.token-scrooge/token-scrooge.db)                   │
└──────────────────────────────────────────────────────────────┘
```

The proxy addon and the FastAPI backend run **in the same Python process**: mitmproxy runs via its async API in a dedicated thread; FastAPI/Uvicorn runs in the main asyncio event loop. They communicate via direct in-process calls to a shared database layer (thread-safe via SQLAlchemy connection pool).

---

## 5. Components

### 5.1 HTTPS Proxy

**Technology:** `mitmproxy` Python library (inline addon API).  
**Port:** `8080` (configurable).  
**Bind address:** `127.0.0.1` only.

#### TLS Interception

- On first `token-scrooge start`, check for `~/.mitmproxy/mitmproxy-ca.pem`; mitmproxy generates it automatically if absent.
- Detect OS and attempt automatic CA trust-store installation:
  - **Windows:** `certutil -addstore Root ~/.mitmproxy/mitmproxy-ca.pem`
  - **macOS:** `security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/.mitmproxy/mitmproxy-ca.pem`
  - **Linux:** copy to `/usr/local/share/ca-certificates/mitmproxy-ca.crt` + `sudo update-ca-certificates`
- If automatic installation fails, print clear manual instructions and continue.
- mitmproxy dynamically signs per-domain certificates using the local CA — no external CA needed.
- The `token-scrooge install-cert` CLI command re-runs this step independently.

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

#### Addon Class: `TokenScroogeAddon`

```python
class TokenScroogeAddon:
    def request(self, flow):
        flow.metadata["ts_start"] = time.monotonic()

    def response(self, flow):
        # 1. Check hostname filter — skip if not an LLM host
        # 2. Extract request/response bodies
        # 3. Detect provider from hostname
        # 4. Detect agent from User-Agent header
        # 5. Run analysis pipeline → get token breakdown
        # 6. Persist record to SQLite
        # 7. Emit WebSocket event for live UI update
```

---

### 5.2 Context Analyser

Runs synchronously after each captured response. Parses the JSON request body according to the provider's API schema and classifies message content into categories.

#### Supported Request Formats

| Provider | Endpoint | Format |
|---|---|---|
| OpenAI | `POST /v1/chat/completions` | OpenAI Chat Completions |
| Anthropic | `POST /v1/messages` | Anthropic Messages API |
| Ollama | `POST /api/chat` | Ollama chat format |
| Copilot | `POST /v1/chat/completions` | OpenAI-compatible |

Requests to unrecognised endpoints for a known host are recorded with `tokens_uncategorized = total_input_tokens` and all other category fields = 0.

#### Content Categories

| Category key | Detection Logic |
|---|---|
| `system_prompt` | Messages with `role == "system"` |
| `tool_definitions` | Top-level `tools` or `functions` array; or content blocks containing JSON objects with `"name"` + `"description"` + (`"input_schema"` or `"parameters"`) keys |
| `tool_results` | Messages with `role == "tool"`, or content blocks with `"type": "tool_result"` (Anthropic), or messages with a `tool_call_id` field |
| `file_contents` | User/assistant messages matched by file-content heuristics (see below) |
| `conversation_history` | All `role == "user"` or `role == "assistant"` turns **except** the last user turn |
| `current_user_message` | The final `role == "user"` message in the array |
| `assistant_prefill` | Any trailing `role == "assistant"` message (Anthropic prompt-caching / prefill pattern) |
| `uncategorized` | Anything not matched by the above |

Classification priority (highest wins when a message could match multiple):  
`tool_results` > `tool_definitions` > `system_prompt` > `assistant_prefill` > `file_contents` > `current_user_message` > `conversation_history` > `uncategorized`

#### File Content Detection Heuristics

A message content string is classified as `file_contents` if **any** of the following match:

1. Contains XML-like tags: `<file_contents>`, `<file>`, `<source>`, `<document_content>` (common in Anthropic prompt patterns).
2. Contains a fenced code block (``` or ~~~) with a filename hint on the opening fence (e.g., ` ```typescript src/foo.ts `).
3. Contains a large fenced code block with ≥ 50 lines of content.
4. Contains lines starting with a comment that looks like a filename path:  
   - `// relative/path/to/file.ext` or `# relative/path/to/file.ext`  
   - followed immediately by code.
5. Message text starts with a path-like string (contains `/` or `\` and a file extension) on its own line, followed by a code block.

These heuristics are intentionally broad. False positives are acceptable in v1; the goal is useful approximation, not perfect categorisation.

---

### 5.3 Token Counter

**Library:** `tiktoken`  
**Encoder:** `cl100k_base` (used as a universal approximation for all providers and models).

**Expected accuracy:**
- OpenAI models (`gpt-4`, `gpt-4o`, `o1`, etc.): exact (this is the native encoder).
- Anthropic Claude models: ~5–15% error for English code/prose; up to ~20% for heavy tool JSON or non-English content.
- Ollama models: ~10–20% error depending on model family (LLaMA, Mistral, Qwen, etc.).

All token count records include a `tokenizer` field set to `"tiktoken/cl100k_base"` so that future re-counting with native tokenizers is possible without schema changes.

**What is counted:**
- Tokens per category in the **input** (prompt) — each category counted separately.
- `tokens_total_input` — sum of all category token counts.
- `tokens_total_output` — token count of the response's `choices[0].message.content` (or Anthropic equivalent).
- If the provider returns a `usage` object in the response body, store `provider_input_tokens` and `provider_output_tokens` alongside estimated counts.

**Counting method:**  
Each category's text is concatenated (preserving JSON structure for tool definitions) and passed to `tiktoken.encode()`. The length of the resulting token list is the count.

---

### 5.4 Storage Layer

**Database:** SQLite at `~/.token-scrooge/token-scrooge.db`  
**ORM:** SQLAlchemy 2.0 (using `mapped_column` / `DeclarativeBase`)  
**Schema initialisation:** SQLAlchemy `create_all()` on startup (no external migration tool in v1).

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
    status_code                     INTEGER,

    -- Estimated token counts by category
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

    -- Provider-reported usage (from response body, may be NULL)
    provider_input_tokens           INTEGER,
    provider_output_tokens          INTEGER,

    tokenizer                       TEXT NOT NULL DEFAULT 'tiktoken/cl100k_base',

    -- Raw content — NULLed out when session ends
    raw_request_body                TEXT,
    raw_response_body               TEXT
);

CREATE INDEX idx_requests_session ON requests(session_id);
CREATE INDEX idx_requests_timestamp ON requests(timestamp);
CREATE INDEX idx_requests_provider ON requests(provider);
```

#### Data Lifecycle

- **During an active session:** `raw_request_body` and `raw_response_body` are populated for every request. This data is used for per-request drill-down in the UI.
- **On session end:** A background task runs:  
  ```sql
  UPDATE requests SET raw_request_body = NULL, raw_response_body = NULL
  WHERE session_id = ?
  ```
- **Requests with no active session:** `session_id = NULL`. Raw bodies are still stored temporarily, but a periodic vacuum (on startup) NULLs out raw bodies for any request older than 24 hours that has no session.
- **Token stats and metadata** are kept indefinitely.

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
| `GET` | `/api/sessions/{id}` | Get session detail + aggregated token stats for that session. |
| `POST` | `/api/sessions/{id}/end` | End a session. Triggers async raw content purge. |
| `DELETE` | `/api/sessions/{id}` | Delete session and all associated request records. |

#### Requests

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/requests` | List requests. Query params: `session_id`, `provider`, `agent`, `limit` (default 50), `offset` (default 0). |
| `GET` | `/api/requests/{id}` | Full request detail including raw bodies (if available). |

#### Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats/overview` | Aggregated totals across all recorded requests. |
| `GET` | `/api/stats/session/{id}` | Aggregated breakdown for a specific session. |
| `GET` | `/api/stats/timeline` | Time-series data. Query params: `session_id` (optional), `bucket` = `minute` \| `hour` \| `day`. |

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
| `GET` | `/api/proxy/status` | `{ "running": bool, "port": 8080, "cert_installed": bool }` |
| `POST` | `/api/proxy/start` | Start the proxy (no-op if already running). |
| `POST` | `/api/proxy/stop` | Stop the proxy. |

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
  - *All agents (general):* `export HTTPS_PROXY=http://127.0.0.1:8080`
  - *GitHub Copilot:* VS Code `settings.json` snippet.
  - *Claude CLI / opencode:* env var instructions.
  - *OpenAI SDK scripts:* env var instructions.

---

### 5.7 CLI

**Entry point:** `token-scrooge` (installed by `pip install -e .` via `pyproject.toml` `[project.scripts]`).  
**CLI framework:** Typer.

```
token-scrooge start [--proxy-port 8080] [--web-port 5173]
    Start both the proxy and web server in the same process.
    Opens browser to http://127.0.0.1:5173 on startup.
    Ctrl+C for clean shutdown.

token-scrooge session start <name>
    Start a named session (calls POST /api/sessions).
    Ends any currently active session first.

token-scrooge session end
    End the active session (calls POST /api/sessions/{active_id}/end).

token-scrooge session list
    Print a table of sessions.

token-scrooge status
    Show whether the proxy is running, active session name, DB path.

token-scrooge install-cert
    Run OS-specific CA cert trust-store installation.
```

`session start`, `session end`, `session list` require the web server to be running (they call the REST API on localhost).

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
| no match | `unknown` |

**Drawbacks:** User-Agent is not guaranteed to be set, is not authenticated, and multiple tools using the same SDK will share the same `agent` label. The `agent` field is informational only; it does not affect analysis or storage logic.

### GitHub Copilot — Special Configuration

Copilot in VS Code may not honour the system `HTTPS_PROXY` environment variable because VS Code uses its own proxy layer. Add to VS Code `settings.json`:

```json
{
  "http.proxy": "http://127.0.0.1:8080",
  "http.proxyStrictSSL": false
}
```

> **Note:** `http.proxyStrictSSL: false` disables TLS verification for VS Code extensions. The mitmproxy CA cert should also be installed system-wide. This is a known limitation of intercepting Copilot traffic and is acceptable for a local development/experimentation tool.

---

## 7. Session Management

### Lifecycle

```
token-scrooge session start "feat/auth-refactor"
        │
        ▼
  INSERT sessions row  (is_active=1)
        │
        ▼
  All proxy captures → session_id = this session
  Raw request/response bodies stored in DB
        │
        ▼
token-scrooge session end   (or UI button)
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
token-scrooge/
├── token_scrooge/                       # Python package
│   ├── __init__.py
│   ├── cli.py                      # Typer CLI entry point
│   ├── config.py                   # Settings (ports, paths, etc.)
│   ├── proxy/
│   │   ├── __init__.py
│   │   ├── addon.py                # mitmproxy TokenScroogeAddon
│   │   ├── cert.py                 # CA cert generation & OS trust-store install
│   │   └── runner.py               # Starts mitmproxy in a background thread
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── classifier.py           # Context classification logic
│   │   ├── tokenizer.py            # tiktoken wrapper
│   │   └── providers.py            # Per-provider request/response parsers
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py               # SQLAlchemy ORM models
│   │   ├── database.py             # Engine + session factory
│   │   └── crud.py                 # Database read/write helpers
│   └── api/
│       ├── __init__.py
│       ├── main.py                 # FastAPI app factory
│       ├── websocket.py            # WebSocket manager
│       └── routers/
│           ├── sessions.py
│           ├── requests.py
│           ├── stats.py
│           └── proxy.py
├── ui/                             # React frontend
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
│   └── vite.config.ts
├── pyproject.toml
├── README.md
└── SPEC.md                         # This file
```

---

## 9. Python Dependencies

`pyproject.toml`:

```toml
[project]
name = "token-scrooge"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mitmproxy>=10.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "sqlalchemy>=2.0",
    "tiktoken>=0.7",
    "typer>=0.12",
    "websockets>=12.0",
]

[project.scripts]
token-scrooge = "token_scrooge.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"
```

Frontend dependencies (`ui/package.json`):
- `react`, `react-dom`, `react-router-dom`
- `@tanstack/react-query`
- `recharts`
- `tailwindcss`, `@tailwindcss/vite`
- `typescript`, `vite`

---

## 10. Configuration

Config file: `~/.token-scrooge/config.toml` (created on first run with defaults).

```toml
[proxy]
port = 8080
bind_addr = "127.0.0.1"

[web]
port = 5173
bind_addr = "127.0.0.1"

[storage]
db_path = "~/.token-scrooge/token-scrooge.db"

[intercepted_hosts]
# Add extra hosts if needed (besides the built-in list)
extra_hosts = []
```

Config values can be overridden by CLI flags. The `config.py` module loads this file and exposes a `Settings` object.

---

## 11. Startup Sequence

When `token-scrooge start` is called:

1. Load and validate config.
2. Ensure `~/.token-scrooge/` directory exists.
3. Initialise SQLite DB (create tables if not exists, run startup vacuum).
4. Check CA cert; if absent, generate via mitmproxy and attempt trust-store installation. If install fails, print instructions.
5. Start mitmproxy `DumpMaster` with `TokenScroogeAddon` in a daemon thread.
6. Start FastAPI/Uvicorn in the main asyncio event loop on `127.0.0.1:5173`.
7. Open `http://127.0.0.1:5173` in the default browser.
8. On `Ctrl+C`: send shutdown signal to mitmproxy thread, wait for it to stop, close DB connections, exit.

---

## 12. Open Questions / Future Work

- **Native tokenizer support:** Anthropic provides a token-counting API endpoint; Ollama has `/api/tokenize`. These could be used for exact counts per provider.
- **Cost estimation:** Add a `models_pricing.json` lookup table (input/output price per 1K tokens per model) to compute estimated cost per request.
- **Streaming responses (SSE):** Currently, only the final `usage` field is captured for streaming responses. Full streaming body reconstruction would allow per-chunk analysis.
- **Export:** CSV / JSON export of session data from the UI.
- **Prompt diffing:** Visual diff of the context window between consecutive requests in the same session.
- **opencode User-Agent:** Confirm the User-Agent string once opencode is available for testing.
- **Copilot proxy behaviour:** Re-examine whether `HTTPS_PROXY` works for Copilot in newer VS Code versions.
- **Re-tokenisation:** Add an API endpoint to re-count tokens for historical requests using a different tokenizer, without re-capturing.
