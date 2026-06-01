# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ContextSpy is a local proxy that sits between a coding agent and an LLM API, records every
request, and classifies the input tokens of each request into 8 categories to show how the
context window is being used. Backend is Python (FastAPI + mitmproxy + SQLAlchemy/SQLite);
frontend is a React/Vite SPA. All data stays local in `~/.contextspy/`.

## Commands

```bash
make build          # uv pip install -e .  +  build the UI into contextspy/_web/
make install        # uv pip install -e . only
make ui             # cd ui && npm install && npm run build (outputs to contextspy/_web/)

make dev-backend    # uvicorn ...api.main:create_app --factory --reload --port 5173
make dev-ui         # cd ui && npm run dev  (Vite on :5174, proxies /api + /ws → :5173)

uv pip install -e ".[dev]"   # install with pytest
pytest                        # run the test suite
pytest tests/test_providers.py::test_name   # run a single test

contextspy start    # production entrypoint: starts proxy + web server together
```

There is no linter/formatter configured. The only tests live in `tests/test_providers.py`
(provider request-parsing). When you touch `analysis/providers.py` or `analysis/classifier.py`,
run pytest.

## Build/packaging gotcha

The React UI is built into `contextspy/_web/`, which is **gitignored** but shipped as package
data (`[tool.setuptools.package-data]` in pyproject.toml). After changing anything under
`ui/src/`, you must run `make ui` (or `cd ui && npm run build`) for the change to appear in the
running app — `contextspy start` serves the pre-built `_web/`, not the Vite dev server. During
active UI work, use `make dev-ui` + `make dev-backend` instead.

## Architecture

### Request flow (the core pipeline)
Both proxy modes feed the same pipeline. The key sequence to understand spans these files:

1. **`proxy/addon.py`** — `ContextSpyAddon` is the mitmproxy addon. It detects the provider
   from host/port (`_HOST_PROVIDER`, `_OLLAMA_PORTS`) and the agent from User-Agent
   (`_UA_AGENTS`), then hands the request/response bodies on.
2. **`analysis/providers.py`** — `parse_request` / `parse_sse_request` normalise the
   provider-specific JSON (OpenAI, Anthropic, Ollama, etc.) into a single `ParsedRequest`
   (list of `ParsedMessage` + serialised tool definitions + provider-reported token counts +
   Anthropic cache breakdown). This is the provider-agnostic boundary — everything downstream
   is provider-independent.
3. **`analysis/classifier.py`** — `classify(parsed)` splits input tokens into the 8 categories
   using heuristics (e.g. `_is_file_content` regexes for detecting embedded file contents).
   Categories: `system_prompt`, `tool_definitions`, `tool_results`, `file_contents`,
   `conversation_history`, `current_message`, `assistant_prefill`, `uncategorized`. Each message
   is assigned to exactly one category by the priority order documented in `classify()`.
   `per_tool_tokens` produces per-tool breakdowns.
4. **`analysis/tokenizer.py`** — `count_tokens` via tiktoken `cl100k_base`. **All counts are
   estimates** (see docs/development.md for per-provider error bands); when the provider reports
   exact counts they are stored alongside.
5. **`db/crud.py` + `db/models.py`** — persisted to SQLite. Then broadcast over WebSocket
   (`api/websocket.py` `ConnectionManager`) so the dashboard updates live.

### Two proxy modes
- **Cloud mode** — mitmproxy as a forward proxy (default port 8888) that TLS-terminates and
  forwards to cloud APIs. Requires the user to install a CA cert (`contextspy install-cert`).
- **Local mode** — mitmproxy as a reverse proxy (default port 8889) in front of a local LLM
  server (Ollama/llama-server/vLLM); plain HTTP, no cert. Uses `provider_override` since the
  upstream host doesn't identify the provider. Launched via `start_local_proxies` in
  `proxy/runner.py`.

`proxy/runner.py` runs mitmproxy on a background thread and watches its logs to confirm the
port actually bound (`_BindWatcher`) — port-in-use is a common failure surfaced to the user.

### Web server
`api/main.py` `create_app(settings)` is an app factory (note `--factory` in the uvicorn
commands). Its lifespan starts the DB and the proxy thread, so running the FastAPI app *is*
running the whole tool. Routers under `api/routers/` (requests, sessions, stats, proxy,
tokenize) back the SPA; the built SPA is served as static files from `contextspy/_web/`.

### CLI
`cli.py` (Typer, entrypoint `contextspy`) is the user-facing surface: `start`, `start-local`,
`status`, `install-cert`, session commands, `report`, `reset-db`, `db-stats`, and
`setup-*` helpers (`setup-claude`, `setup-copilot`, `setup-ollama`, `setup-vllm`, etc.) that
write the proxy/base-url config into each agent.

### Frontend
`ui/src/` — React + react-router + @tanstack/react-query + recharts + Tailwind. Data via
`api/client.ts` (REST) and `api/useWebSocket.ts` (live updates). Pages in `pages/`
(Dashboard, Requests, RequestDetail, Sessions, SessionDetail, Settings); the context-window
visual block map lives in components like `ContextBar`, `TokenDonut`, `ToolBreakdown`.

## Reference docs
- `SPEC.md` — full product/technical spec. `PLAN.md` — implementation plan.
- `docs/development.md` — architecture diagrams, data storage layout, token accuracy bands.
- `docs/` also has install/cloud-mode/local-mode/examples/cli guides.
- `~/.contextspy/`: `contextspy.db` (SQLite), `config.toml` (auto-created). Raw request bodies
  are purged 7 days after capture on server startup (`startup_vacuum`).
