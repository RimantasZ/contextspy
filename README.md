# Token-Scrooge

A local HTTPS proxy that intercepts traffic between coding agents (GitHub Copilot, Claude,
Cursor, etc.) and LLM provider APIs, then analyses context composition and token usage.

## Features

- **HTTPS interception** via mitmproxy — transparent to your agent
- **Provider support**: OpenAI, Anthropic (Claude), Ollama
- **Agent detection**: Copilot, Claude Desktop, Cursor, and generic clients
- **Context analysis**: breaks input tokens into 8 categories:
  - System prompt, Tool definitions, Tool results, File contents,
    Conversation history, Current user message, Assistant prefill, Uncategorised
- **Token estimation** via tiktoken (`cl100k_base`)
- **Live dashboard** — real-time WebSocket updates, charts, session grouping
- **Session tracking** — manually start/end named sessions to group requests
- **SQLite storage** — all data stored locally in `~/.token-scrooge/`

## Quick start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Node.js 18+ and npm (for the UI build — pre-built `ui/dist/` is included)

### Install

```bash
git clone https://github.com/you/token-scrooge.git
cd token-scrooge
uv venv
uv pip install -e .
```

### Build the UI (optional — only needed if you change the frontend)

```bash
cd ui
npm install
npm run build
cd ..
```

### Run

```bash
token-scrooge start
```

This will:
1. Start the mitmproxy HTTPS proxy on port **8080**
2. Start the FastAPI web server on port **5173**
3. Open the dashboard at http://127.0.0.1:5173 in your browser

### Install the CA certificate

On first run, configure your system/agent to trust the mitmproxy CA certificate:

```bash
token-scrooge install-cert
```

Or from the dashboard → Settings → Proxy tab.

### Configure your agent to use the proxy

**GitHub Copilot (VS Code)**

Add to VS Code `settings.json`:
```json
{
  "http.proxy": "http://127.0.0.1:8080"
}
```

**Environment variable** (works for Claude CLI, curl, httpx, etc.):
```bash
export HTTPS_PROXY=http://127.0.0.1:8080
export HTTP_PROXY=http://127.0.0.1:8080
```

## CLI reference

```
token-scrooge start           # start server (opens browser)
token-scrooge status          # show proxy / server status
token-scrooge install-cert    # install mitmproxy CA cert into OS trust store

token-scrooge session start   # start a named capture session
token-scrooge session end     # end the active session
token-scrooge session list    # list all sessions
```

## Architecture

```
┌──────────────────────────────────────────┐
│  coding agent (Copilot, Claude CLI, …)   │
└───────────────┬──────────────────────────┘
                │ HTTPS via proxy (port 8080)
┌───────────────▼──────────────────────────┐
│  mitmproxy (DumpMaster, daemon thread)   │
│  TokenScroogeAddon                       │
│    → parse provider/agent                │
│    → classify context categories         │
│    → count tokens (tiktoken)             │
│    → write to SQLite                     │
│    → broadcast via WebSocket             │
└───────────────┬──────────────────────────┘
                │ SQLAlchemy (thread-safe)
┌───────────────▼──────────────────────────┐
│  FastAPI (port 5173, main asyncio loop)  │
│  REST API  /api/…                        │
│  WebSocket /api/ws                       │
│  Static    /  → ui/dist/                 │
└───────────────┬──────────────────────────┘
                │ React + TanStack Query
┌───────────────▼──────────────────────────┐
│  Browser dashboard                       │
│  Dashboard · Requests · Sessions         │
│  Settings (proxy config + cert install)  │
└──────────────────────────────────────────┘
```

## Data storage

All data is stored in `~/.token-scrooge/`:

| Path | Description |
|------|-------------|
| `~/.token-scrooge/token-scrooge.db` | SQLite database |
| `~/.token-scrooge/config.toml` | Configuration (auto-created) |

Raw request/response bodies are stored per-request and purged automatically
24 hours after a session ends to save disk space.

## Token estimation accuracy

Token counts are **estimates** using tiktoken `cl100k_base` encoding.
Accuracy varies by provider:

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-4, GPT-4o) | ~2–5% |
| Anthropic (Claude) | ~5–15% |
| Ollama (Llama, Mistral, …) | ~10–20% |

When the provider reports exact token counts in the API response, those are
stored alongside the estimate for comparison on the request detail page.

## Development

### Backend

```bash
uv venv
uv pip install -e ".[dev]"   # add [dev] extras if defined
uvicorn token_scrooge.api.main:create_app --factory --reload --port 5173
```

### Frontend

```bash
cd ui
npm install
npm run dev   # Vite on :5174, proxies /api to :5173
```

## License

MIT
