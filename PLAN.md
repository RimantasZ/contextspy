# ContextSpy — Implementation Plan

> Ordered phases. Each phase is independently testable before moving on.  
> Reference: [SPEC.md](SPEC.md)

---

## Phase 0 — Project Scaffold

**Goal:** Runnable skeleton with correct directory structure and dependency manifests. No logic yet.

### Tasks

- [ ] Create `pyproject.toml` with all Python dependencies and `[project.scripts]` entry point (SPEC §9).
- [ ] Create `contextspy/` package with empty `__init__.py` files in every sub-package (`proxy/`, `analysis/`, `db/`, `api/`, `api/routers/`).
- [ ] Create `contextspy/config.py` — `Settings` dataclass with all config fields and defaults; reads from `~/.ContextSpy/config.toml` if present, overridable by kwargs (SPEC §10).
- [ ] Initialise frontend: `cd ui && npm create vite@latest . -- --template react-ts`, add Tailwind, React Router, TanStack Query, Recharts to `package.json`.
- [ ] Add `vite.config.ts` with API proxy to `http://127.0.0.1:5173` for dev mode.
- [ ] Verify: `uv pip install -e .` succeeds; `ContextSpy --help` prints help text; `cd ui && npm install` succeeds.

---

## Phase 1 — Storage Layer

**Goal:** Database initialises, records can be written and read back. Nothing else runs yet.

### Tasks

- [ ] `contextspy/db/models.py` — SQLAlchemy 2.0 `DeclarativeBase` models for `Session` and `Request` tables with all columns from SPEC §5.4 schema.
- [ ] `contextspy/db/database.py` — create engine (`~/.ContextSpy/ContextSpy.db`), `sessionmaker`, `get_db()` dependency, `init_db()` (calls `create_all`), `startup_vacuum()` (NULLs raw bodies for session-less requests older than 24 h).
- [ ] `contextspy/db/crud.py` — implement all read/write helpers:
  - `create_session(name) → Session`
  - `get_active_session() → Session | None`
  - `end_session(id)` — sets `ended_at`, `is_active=0`
  - `purge_raw_bodies(session_id)` — background task NULLing raw content
  - `create_request(data) → Request`
  - `get_request(id) → Request | None`
  - `list_requests(filters, limit, offset) → list[Request]`
  - `get_stats(session_id=None) → StatsDict`
  - `get_timeline(session_id, bucket) → list[TimelineBucket]`
- [ ] Verify: write a quick `python -c` snippet that calls `init_db()`, inserts a session and a request, queries them back, and prints results.

---

## Phase 2 — Analysis Pipeline

**Goal:** Given a raw HTTP request/response body pair and provider name, produce a complete token breakdown dict. Fully testable in isolation with no proxy running.

### Tasks

- [ ] `contextspy/analysis/tokenizer.py` — wrap `tiktoken.get_encoding("cl100k_base")`; expose `count_tokens(text: str) → int`.
- [ ] `contextspy/analysis/providers.py` — per-provider parsers returning a normalised `ParsedRequest` dataclass:
  - `parse_openai(req_body, resp_body) → ParsedRequest` (handles OpenAI + Copilot format)
  - `parse_anthropic(req_body, resp_body) → ParsedRequest`
  - `parse_ollama(req_body, resp_body) → ParsedRequest`
  - `ParsedRequest` fields: `model`, `messages`, `tools`, `provider_input_tokens`, `provider_output_tokens`, `response_text`
  - Handle streaming responses: if response body is SSE, extract `usage` from the final `data: [DONE]` chunk only.
- [ ] `contextspy/analysis/classifier.py` — implement `classify(parsed: ParsedRequest) → CategoryBreakdown`:
  - Apply priority rules from SPEC §5.2 to assign each message/tool block to exactly one category.
  - Implement all 5 file-content heuristics.
  - Return `CategoryBreakdown` dataclass with one `int` field per category + `total_input` + `total_output`.
- [ ] Verify: write unit tests (or inline assertions) covering:
  - OpenAI request with system prompt + tools + multi-turn history + file in user message.
  - Anthropic request with tool use + tool results + prefill.
  - Ollama plain chat request.
  - Empty / unrecognised endpoint body.

---

## Phase 3 — Proxy

**Goal:** mitmproxy runs, intercepts LLM traffic, runs the analysis pipeline, and writes to the DB.

### Tasks

- [ ] `contextspy/proxy/cert.py`:
  - `cert_exists() → bool` — checks for `~/.mitmproxy/mitmproxy-ca-cert.pem`.
  - `install_cert() → (success: bool, message: str)` — OS-detecting logic for Windows (`certutil`), macOS (`security`), Linux (`update-ca-certificates`); falls back to printing manual instructions.
- [ ] `contextspy/proxy/addon.py` — `ContextSpyAddon`:
  - `request()` — timestamps the flow.
  - `response()` — hostname filter, provider/agent detection, call analysis pipeline, call `crud.create_request()`, emit WebSocket event (call into `api.websocket` manager via a shared reference set at startup).
  - Handle JSON decode errors and analysis exceptions silently (log, don't crash the proxy).
- [ ] `contextspy/proxy/runner.py`:
  - `start_proxy(settings, ws_manager, db_session_factory)` — builds `DumpMaster` with `ContextSpyAddon`, runs in a `threading.Thread(daemon=True)`.
  - `stop_proxy()` — calls `master.shutdown()`, joins thread with 3 s timeout.
  - Exposes `is_running() → bool`.
- [ ] Verify: run the proxy standalone, set `HTTPS_PROXY=http://127.0.0.1:8080`, make a `curl` call to `https://api.openai.com` (will fail auth, that's fine), confirm the flow appears in the DB.

---

## Phase 4 — REST API & WebSocket

**Goal:** All API endpoints return correct data; WebSocket broadcasts live events. Testable with `curl` or a REST client.

### Tasks

- [ ] `contextspy/api/websocket.py` — `ConnectionManager`: `connect`, `disconnect`, `broadcast(message: dict)` methods; thread-safe (proxy writes from a thread, FastAPI reads from async context — use `asyncio.run_coroutine_threadsafe`).
- [ ] `contextspy/api/main.py` — FastAPI app factory:
  - Lifespan: `startup` initialises DB, starts proxy; `shutdown` stops proxy, disposes engine.
  - Mount static files from `ui/dist/` at `/` (fallback to `index.html` for SPA routing).
  - Include all routers under `/api`.
  - Register `ConnectionManager` as app state.
- [ ] `contextspy/api/routers/sessions.py` — all session endpoints (SPEC §5.5).
- [ ] `contextspy/api/routers/requests.py` — list + detail endpoints.
- [ ] `contextspy/api/routers/stats.py` — overview, per-session, timeline.
- [ ] `contextspy/api/routers/proxy.py` — status, start, stop endpoints.
- [ ] WebSocket endpoint at `GET /api/ws`.
- [ ] Verify: start FastAPI with `uvicorn contextspy.api.main:app --port 5173`, hit all endpoints with `curl`, confirm WebSocket broadcasts using `wscat` or a browser console.

---

## Phase 5 — CLI

**Goal:** `ContextSpy start` launches everything and the browser opens. All sub-commands work.

### Tasks

- [ ] `contextspy/cli.py` — Typer app with commands:
  - `start` — runs `init_db()` + `startup_vacuum()`, installs cert if missing (with prompt), starts Uvicorn programmatically in main thread (which triggers lifespan → proxy start), opens browser.
  - `session start <name>` — POST `/api/sessions`.
  - `session end` — POST `/api/sessions/{active_id}/end`.
  - `session list` — GET `/api/sessions`, print as table using `rich`.
  - `status` — GET `/api/proxy/status` + print active session info.
  - `install-cert` — standalone cert install, prints result.
- [ ] Add `rich` to dependencies for table/status output.
- [ ] Verify: full end-to-end — `ContextSpy start`, browser opens, `ContextSpy session start "test"` in a second terminal, proxy intercepts a real request, dashboard updates.

---

## Phase 6 — Frontend Scaffold

**Goal:** React app loads, routing works, API layer is wired, no blank screens.

### Tasks

- [ ] `ui/src/main.tsx` — wrap app in `QueryClientProvider` + `BrowserRouter`.
- [ ] `ui/src/App.tsx` — define all routes matching SPEC §5.6 pages.
- [ ] `ui/src/api/` — typed fetch wrappers and TanStack Query hooks:
  - `useSessions()`, `useSession(id)`, `useCreateSession()`, `useEndSession(id)`
  - `useRequests(filters)`, `useRequest(id)`
  - `useStats(sessionId?)`, `useTimeline(sessionId?, bucket)`
  - `useProxyStatus()`
  - `useWebSocket()` — connects to `ws://127.0.0.1:5173/api/ws`, exposes latest event; invalidates relevant queries on `new_request` / `session_*` events.
- [ ] Shared layout component: sidebar nav (Dashboard, Requests, Sessions, Settings), header.
- [ ] Placeholder page components for all 6 routes (just renders the page title).
- [ ] Verify: `npm run dev`, all routes navigate without errors, API calls succeed (check Network tab).

---

## Phase 7 — UI Pages

**Goal:** All pages are fully functional with real data, charts, and interactions. Implement in order of dependency.

### Tasks

#### 7a — Shared Components
- [ ] `TokenDonut.tsx` — Recharts `PieChart` (innerRadius for donut), one slice per category, fixed colour palette, tooltip with token count + %.
- [ ] `TimeSeriesChart.tsx` — Recharts `LineChart`, x-axis as time, y-axis as total input tokens, bucket selector (minute/hour/day).
- [ ] `RequestTable.tsx` — sortable table, provider colour badge, clickable rows.
- [ ] `SessionControls.tsx` — Start Session button (modal with name input) + End Session button; wired to mutations; shows active session name in header.
- [ ] `RawViewer.tsx` — collapsible JSON panel with syntax highlight; shows purge notice if content is null.

#### 7b — Dashboard (`/`)
- [ ] Summary stat cards (request count, total input tokens, total output tokens).
- [ ] `TokenDonut` with current session stats (falls back to all-time if no active session).
- [ ] `TimeSeriesChart` for current session.
- [ ] Recent requests table (last 20, live-updating via WebSocket query invalidation).
- [ ] `SessionControls` in header.

#### 7c — Requests (`/requests`)
- [ ] Filter bar: session dropdown, provider multi-select, agent multi-select.
- [ ] Paginated `RequestTable` (50/page, prev/next).

#### 7d — Request Detail (`/requests/:id`)
- [ ] `TokenDonut` scoped to single request.
- [ ] Category breakdown table.
- [ ] Estimated vs. provider-reported comparison table (conditional).
- [ ] `RawViewer` for request body + response body.

#### 7e — Sessions (`/sessions`) & Session Detail (`/sessions/:id`)
- [ ] Sessions list table.
- [ ] Session detail: `TokenDonut` + `TimeSeriesChart` + requests table + End/Delete buttons with confirmation dialog.

#### 7f — Settings (`/settings`)
- [ ] Proxy config card (port display, restart note).
- [ ] CA cert status badge + install button (calls `POST /api/proxy/...` or dedicated endpoint) + manual instructions collapsible.
- [ ] Agent setup tabs (Copilot / Claude / opencode / OpenAI SDK).

---

## Phase 8 — Integration & Polish

**Goal:** Production build served by FastAPI, cross-platform smoke test, rough edges fixed.

### Tasks

- [ ] Build frontend: `cd ui && npm run build` → outputs to `ui/dist/`.
- [ ] Verify FastAPI serves the built UI correctly at `http://127.0.0.1:5173` (SPA fallback for deep links).
- [ ] Update `pyproject.toml` to include a `build` script / Makefile target that runs `npm run build` before packaging.
- [ ] Smoke test on Windows: full flow — install, start, make a proxied OpenAI request, check dashboard.
- [ ] Smoke test on macOS/Linux (if available).
- [ ] Graceful shutdown verification: Ctrl+C stops mitmproxy thread cleanly, no port left open.
- [ ] Startup vacuum verification: session-less requests older than 24 h have raw bodies NULLed.
- [ ] Error path verification: analysis failure does not crash the proxy; bad JSON body is logged and skipped.
- [ ] Write `README.md`: prerequisites, install steps, quick-start, agent configuration table, cert setup instructions per OS.

---

## Dependency Graph

```
Phase 0 (scaffold)
    └── Phase 1 (storage)
            └── Phase 2 (analysis)     ← no DB dependency, can start after Phase 0
            └── Phase 3 (proxy)        ← needs Phase 1 + Phase 2
            └── Phase 4 (REST API)     ← needs Phase 1 + Phase 3
                    └── Phase 5 (CLI)  ← needs Phase 4
Phase 6 (frontend scaffold)            ← can start after Phase 0, parallel to Phases 1–5
    └── Phase 7 (UI pages)             ← needs Phase 4 + Phase 6
Phase 8 (integration)                  ← needs all phases
```

**Phases 2 and 6 can be worked in parallel with the backend phases.**

---

## Suggested Prompt Chunking for a Coding Agent

Feed phases one at a time. Suggested prompt prefix for each:

> "Implement Phase N of the ContextSpy project as described in PLAN.md, following the detailed specifications in SPEC.md. Only implement what is listed in Phase N — do not add features from later phases."

Start with Phase 0 to get the skeleton right, then Phase 1 (storage) and Phase 2 (analysis) can be given together or separately. Phases 3–5 must be sequential. Phase 6 can run in parallel as a separate task.
