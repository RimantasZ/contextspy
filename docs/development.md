# Development

## Backend

```bash
git clone https://github.com/RimantasZ/contextspy.git
cd contextspy
uv venv
uv pip install -e ".[dev]"
uvicorn contextspy.api.main:create_app --factory --reload --port 5173
```

## Frontend

```bash
cd ui
npm install
npm run dev   # Vite dev server on :5174, proxies /api and /ws to :5173
```

The built UI is embedded in the Python package at `contextspy/_web/`. Rebuild after
changing anything under `ui/src/`:

```bash
cd ui && npm run build   # outputs to contextspy/_web/
```

---

## Architecture

### Cloud mode

```
coding agent → HTTPS_PROXY → mitmproxy (port 8888)
                                  │ TLS terminate + forward
                              cloud LLM API
                                  │
                            ContextSpyAddon
                              → parse request body
                              → classify tokens into 8 categories
                              → write to SQLite
                              → broadcast via WebSocket
```

### Local mode

```
client (base_url=:8889) → mitmproxy reverse proxy (port 8889)
                                  │ plain HTTP forward
                            llama-server / Ollama / vLLM (port 8080…)
                                  │
                            ContextSpyAddon (provider_override="openai")
                              → parse, classify, count tokens
                              → write to SQLite
                              → broadcast via WebSocket
```

Both modes share the same FastAPI web server (port 5173), SQLite database, and dashboard.

---

## Data storage

All data is stored in `~/.contextspy/`:

| Path | Description |
|------|-------------|
| `~/.contextspy/contextspy.db` | SQLite database — all requests and sessions |
| `~/.contextspy/config.toml` | Configuration file (auto-created on first run) |

Raw request/response bodies are stored per-request and purged automatically 7 days after
capture (on next server startup) to limit disk usage.

---

## Token estimation accuracy

Token counts are **estimates** using tiktoken `cl100k_base` encoding.

| Provider | Expected error |
|----------|----------------|
| OpenAI (GPT-4, GPT-4o) | ~2–5% |
| Anthropic (Claude) | ~5–15% |
| Ollama / llama.cpp / vLLM | ~10–20% |

When the provider reports exact token counts in the API response, those are stored
alongside the estimate and shown on the request detail page for comparison.
