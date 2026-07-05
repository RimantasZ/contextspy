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
(adapter request-parsing + classifier + block persistence). When you touch `analysis/adapters/`
or `analysis/classifier.py`, run pytest.

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
2. **`analysis/adapters/`** — `get_adapter(endpoint)` dispatches by request path (not host) to a
   `WireFormatAdapter` (`anthropic.py`, `openai_chat.py`, `openai_responses.py`, `ollama.py`).
   Each adapter's `parse_request`/`parse_response`/`parse_sse` turns provider-specific JSON into
   provider-agnostic `Block`s (`analysis/blocks.py`) + a `Usage` — this is the provider-agnostic
   boundary. Adding a new provider/wire format is a new adapter module, nothing else changes.
3. **`analysis/classifier.py`** — `classify(analyzed)` assigns each input `Block` a `category`
   (`classify_blocks`) using heuristics (e.g. `_is_file_content` regexes for detecting embedded
   file contents) and aggregates into the 8 categories: `system_prompt`, `tool_definitions`,
   `tool_results`, `file_contents`, `conversation_history`, `current_user_message`,
   `assistant_prefill`, `uncategorized`, plus `tokens_output_text`/`tokens_output_thinking` on the
   output side. Priority order for category assignment is documented in `classify_blocks()`.
   `per_tool_tokens` produces per-tool breakdowns.
4. **`analysis/tokenizer.py`** — `count_tokens` via tiktoken `cl100k_base`. **All counts are
   estimates** (see docs/development.md for per-provider error bands); when the provider reports
   exact counts they are stored alongside.
5. **`db/crud.py` + `db/models.py`** — the `Request` row (aggregate token counts) plus one
   `Block` row per content part (`db/models.py: BlockRecord`), content-addressed into
   `block_contents` for dedup across a session. Then broadcast over WebSocket
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
`status`, `install-cert`, session commands, `report`, `reset-db`, `db-upgrade`, `db-stats`, and
`setup-*` helpers (`setup-claude`, `setup-copilot`, `setup-ollama`, `setup-vllm`, etc.) that
write the proxy/base-url config into each agent.

### Database schema changes — REQUIRED steps

`db/database.py: init_db()` only creates *new* tables automatically (`Base.metadata.create_all`
does not add columns to existing tables). Any change to `db/models.py` — a new column on an
existing table, a new table whose rows need populating for pre-existing data, or any change that
existing databases won't already have — **must** also touch `db/migrations.py`:

1. **New column on an existing table** (e.g. `Request`, `BlockRecord`): add it to the
   `new_columns` list in `db/database.py: _migrate()` (additive `ALTER TABLE`, applied on every
   startup — this part runs automatically for all users, no version bump needed on its own).
2. **New derived/backfillable data** (a new column or table that needs values computed from
   existing rows, e.g. the `session_seq` and `blocks` backfill in `_migrate_to_v2`): bump
   `SCHEMA_VERSION` in `db/migrations.py`, add a new `_migrate_to_vN` function, and register it in
   `_DATA_MIGRATIONS`. This is NOT automatic — it only runs when the user explicitly invokes
   `contextspy db-upgrade` (see `check_and_flag_pending_migrations` / `apply_data_migrations`).
   `cli.py: start`/`start-local` refuse to boot (`_abort_if_migrations_pending`) until this is
   applied or the user runs `reset-db`.

Forgetting step 1 crashes every command that touches the DB with
`OperationalError: no such column: ...` on any pre-existing database — this has happened before.
Forgetting step 2 means existing requests silently never get the new derived data.

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
  and block contents are purged after capture on server startup (`startup_vacuum`), per the
  `[retention]` settings in `config.toml` (default 7 days for both; 0 = keep forever).
