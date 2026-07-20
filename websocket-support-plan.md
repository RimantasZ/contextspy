# Native WebSocket Capture — Design & Implementation Plan

## Context

Codex CLI with ChatGPT-plan auth uses a WebSocket transport to `chatgpt.com/backend-api/codex/responses`. ContextSpy's mitmproxy addon only implements HTTP hooks, so those turns are invisible — users currently need a `~/.codex/config.toml` workaround (`supports_websockets = false`) that we document in `setup-codex`. This plan adds native WS capture so the workaround becomes unnecessary, with a pluggable architecture so future WS-speaking providers (e.g. OpenAI Realtime) are one new module, mirroring how wire-format adapters work today.

**Codex WS protocol** (verified against `openai/codex` source, `codex-rs/codex-api/src/endpoint/responses_websocket.rs` + `common.rs`):
- Client → server: one TEXT frame per turn = standard Responses API request JSON plus `"type": "response.create"` at top level (harmless to the existing `parse_request`).
- Server → client: TEXT frames, each one bare `response.*` event JSON — identical event objects to SSE `data:` payloads. Turn ends at `response.completed`. Codex-only extras to tolerate: `codex.rate_limits` frames and error envelope `{"type":"error","status":<int>,"error":{code,message}}`.
- Connections are pooled and reused across turns (multiple sequential exchanges per connection).

**mitmproxy facts** (verified in installed 12.2.2 source):
- Hooks `websocket_start/message/end(flow)`; newest message = `flow.websocket.messages[-1]` (`WebSocketMessage`: `from_client`, `content: bytes`, `is_text`, `timestamp`).
- `flow.websocket` is set **before** the `response` hook fires (`proxy/layers/http/__init__.py:506-509`) — sanctioned way to suppress the junk 101-upgrade row.
- After `WebsocketMessageHook`, forwarding uses a local variable, not the list (`proxy/layers/websocket.py:184-191`) — `del flow.websocket.messages[:-1]` in our hook is safe and bounds memory.
- The `websocket` option defaults to True; no runner changes needed.

**Key pipeline facts**: `_save_request` touches the flow in only 2 places (`status_code`, `raw_request_body`) — everything downstream (`classify` → crud → dashboard broadcast) is transport-neutral. `OpenAIResponsesAdapter.parse_sse` is SSE-line framing + event semantics fused together; WS needs the semantics without the framing.

## Architecture

Three layers, mirroring the existing adapter pattern:

1. **Event-level adapter API** (`analysis/adapters/`): new `parse_events(events: list[dict])` method — same semantics as `parse_sse` minus SSE framing. Framing helper `extract_sse_events(raw)` moves to base; `parse_sse` becomes a thin wrapper.
2. **WS protocol registry** (`proxy/ws_protocols/`, new package): per-provider frame-stream → exchange assemblers. Transport concern (connections, direction, time), so it lives in `proxy/`, but with **no mitmproxy imports** — sessions consume scalars, unit-testable without a proxy.
3. **Addon hooks** (`proxy/addon.py`): `websocket_start/message/end` glue registry → adapter → existing save path (decoupled from the flow object).

Adding a new WS provider later = one protocol module in `ws_protocols/` + `parse_events` on its adapter.

## Steps (dependency order)

### 1. `contextspy/analysis/adapters/base.py`
- Add module-level `extract_sse_events(raw: bytes) -> list[dict]` — the tolerant `data: `-line loop currently at `openai_responses.py:178-186` (skip `[DONE]`, blanks, bad JSON).
- Add non-abstract `parse_events(self, events: list[dict]) -> tuple[list[Block], Usage]` on `WireFormatAdapter`, default `raise NotImplementedError(f"{self.format_id} has no event-level parser")`. (Non-abstract so anthropic/openai_chat/ollama don't need stubs; addon catches `NotImplementedError` and degrades gracefully.)

### 2. `contextspy/analysis/adapters/openai_responses.py`
Mechanical refactor, zero behavior change: `parse_sse(raw)` → `return self.parse_events(extract_sse_events(raw))`; body of current `parse_sse` (accumulators, `response.completed` usage, hidden-thinking synthesis) becomes `parse_events` iterating dicts. Unknown event types already ignored by dispatch. **Regression gate: existing `TestOpenAIResponsesAdapter` tests pass unchanged.** Other adapters untouched.

### 3. New package `contextspy/proxy/ws_protocols/`
`base.py`:
```python
@dataclass
class CompletedExchange:
    request_body: dict          # request JSON (may be synthesized by a protocol)
    raw_request_text: str       # verbatim client frame → raw_request_body
    events: list[dict]          # decoded server events, arrival order
    request_ts: float | None
    first_event_ts: float | None
    last_event_ts: float | None
    error: dict | None = None   # {"status", "code", "message"}
    complete: bool = True       # False when flushed (close / superseded)

class WsSession(ABC):           # one instance per connection, stateful
    def on_message(self, *, from_client: bool, content: bytes,
                   is_text: bool, timestamp: float) -> list[CompletedExchange]: ...
    def on_close(self) -> list[CompletedExchange]: ...

class WsProtocol(ABC):
    protocol_id: str
    host_patterns: tuple[str, ...]   # exact-or-suffix match (same rule as _detect_provider); () = any
    path_patterns: tuple[str, ...]   # substring match, like adapter endpoint_patterns
    def new_session(self) -> WsSession: ...
```
Plus `WS_REGISTRY` / `register_ws_protocol` / `get_ws_protocol(host, path)` (first match wins). Signature notes: `content: bytes` + `is_text` (not `text: str`) and `list[...]` returns are deliberate — binary frames and overlapping responses are what OpenAI Realtime would need.

`codex.py` — `CodexResponsesProtocol` (`host_patterns=("chatgpt.com",)`, `path_patterns=("/backend-api/codex/responses",)`) and `CodexResponsesSession` holding one `_pending` exchange:
- Non-text / unparseable / non-dict frames → debug log, `[]`.
- Client `"type": "response.create"` → flush any pending as `complete=False`, open new pending (`request_body=obj`, verbatim `raw_request_text`, `request_ts=timestamp`); return flushed or `[]`.
- Server frame with no pending (e.g. idle `codex.rate_limits`) → `[]`.
- Server frame with pending: update `first_event_ts`/`last_event_ts` from frame timestamp first (so ttft = true first frame), then: `codex.rate_limits` → skip; `"error"` → set `pending.error`, finalize `complete=True`; else append event, finalize on `response.completed`.
- `on_close()` → flush pending as `complete=False`.

`__init__.py` mirrors `adapters/__init__.py` (registry + extension recipe in docstring).

### 4. DB: `contextspy/db/models.py` + `contextspy/db/database.py`
- `Request`: `transport: Mapped[str] = mapped_column(String, nullable=False, default="http", server_default="http")`; add to `to_dict()` (flows to API + dashboard broadcast automatically).
- Per CLAUDE.md additive-migration rule: append `("transport", "TEXT NOT NULL DEFAULT 'http'")` to `new_columns` in `database.py:_migrate()`. No SCHEMA_VERSION bump (no backfill needed — old rows correctly read `'http'`).

### 5. `contextspy/proxy/addon.py`
- **Decouple `_save_request`**: drop the `flow` param; add explicit `status_code: int | None`, `raw_request_body: str | None`, `transport: str = "http"` kwargs. Update the two HTTP callers.
- **Suppress junk 101 row**: top of `response()`: `if flow.websocket is not None: return`.
- **Agent detection**: add `("codex", "codex")` to `_UA_AGENTS` (Codex UA is `codex_cli_rs/<ver>`; no collision with existing patterns). For WS, detect on `f"{ua} {flow.request.headers.get('originator', '')}"` (Codex also sends `originator: codex_cli_rs`).
- **WS hooks** + per-flow state `self._ws_flows: dict[str, _WsFlowState]` (dataclass: session/provider/agent/endpoint; no locking — hooks run on the addon's own DumpMaster loop; `provider_override` for reverse mode works for free via `_get_provider`):
  - `websocket_start`: provider gate via `_get_provider`; `get_ws_protocol(host, path)`; if provider matched but no protocol, `logger.info` (unsupported WS provider isn't silently invisible); else store state.
  - `websocket_message`: feed `messages[-1]` to `session.on_message(...)` (wrapped in try/except — never crash the relay); then `del flow.websocket.messages[:-1]` (memory bound); each returned exchange → `_handle_ws_exchange`.
  - `websocket_end`: pop state, `on_close()`, handle flushed exchanges.
  - `error(flow)`: belt-and-braces — delegate to `websocket_end` if flow is tracked.
- **`_handle_ws_exchange(state, ex)`**: `get_adapter(state.endpoint)` → `parse_request(ex.request_body)` + `parse_events(ex.events)` (catch `NotImplementedError` → empty output + `Usage()`, warn once) → `AnalyzedRequest`; stash `ex.error` as `usage.extra["ws_error"]`, `ex.complete=False` as `usage.extra["ws_incomplete"]`. `duration_ms`/`ttft_ms` from exchange timestamps. Response text via existing `_synthetic_response_text` (fallback `json.dumps(ex.events)`). Save with `transport="websocket"`, `status_code=(ex.error or {}).get("status")` (None on success — don't fake a 200 or reuse 101), `raw_request_body=ex.raw_request_text`. Existing `_LLM_PATHS` gate passes (`/responses` substring).

### 6. UI (`make ui` rebuild required)
- `ui/src/api/client.ts`: add `transport: string` to `Request` interface (~line 49).
- `ui/src/components/RequestTable.tsx`: in the Status cell (~line 227), render a small `WS` badge when `req.transport === 'websocket'` (alongside status badge if `status_code` non-null — error case; alone otherwise). Add `codex` to `AGENT_COLORS`.
- `ui/src/pages/RequestDetail.tsx` (~line 89): show Transport row when websocket.

### 7. CLI text + docs
- `cli.py` `setup-codex` (~1130-1149): WS transport is now captured natively; demote the `chatgpt_http` config.toml block to a legacy fallback and tell prior users they can remove it.
- `docs/faq.md` WebSocket answer (~68-83): rewrite — supported for registered WS protocols (currently Codex CLI / ChatGPT-plan); WS turns show a transport marker and no HTTP status.
- `docs/cloud-mode.md` Codex section (~161-179): same; workaround now optional/legacy.
- `docs/changelog.md`: native WS capture, `transport` column, codex agent detection.

### 8. Tests
New `tests/test_ws_protocols.py` (house style: module-level builders `_make_codex_request_frame()`, `_make_codex_event_frames()`):
- `TestGetWsProtocol`: match, wrong host, wrong path, subdomain suffix.
- `TestCodexSession`: single turn (empty until `completed`, then one exchange with correct body/events/timestamps); multi-turn on one connection (no event bleed); rate_limits skipped + idle server frame ignored; error envelope (status/code/message, immediate finalize); new request flushes dangling (`complete=False`); close flushes dangling / idle close empty; binary, garbage-JSON, non-dict frames ignored.

Additions to `tests/test_providers.py`:
- `extract_sse_events` unit tests (data lines, `[DONE]`, blanks, bad JSON).
- `test_parse_events_equivalent_to_parse_sse` for openai_responses.
- Agent detection: `codex_cli_rs/0.46.0` → `codex`.
- Persistence: `create_request` with `transport='websocket'` round-trips; omitted defaults to `'http'`; `init_db` twice on same tmp path (migration idempotence).
- Optional: addon-level `_handle_ws_exchange` test against `init_db(tmp_path)` (no mitmproxy master needed) asserting persisted row.

## Edge cases

| Case | Handling |
|---|---|
| Pooled connection, multiple turns | session persists; one row per `response.completed` |
| `response.create` while turn in flight | previous flushed `complete=False` (`ws_incomplete`), new opens |
| Close / 1006 / proxy error mid-turn | `websocket_end` + `error` hook flush dangling exchange |
| Error envelope | `status_code=<envelope status>`, `ws_error` in usage_extra |
| Binary / garbage / unknown frames | debug-logged, ignored; all session calls try/except-wrapped |
| Non-codex WS on LLM host | logged at info; upgrade row suppressed |
| Long-lived connection memory | `del flow.websocket.messages[:-1]` per message (verified safe) |

## Verification

1. `uv run pytest` — all 72 existing + new tests pass (step 2 gated by unchanged `TestOpenAIResponsesAdapter`).
2. `make ui` builds clean; `make build` for the packaged app.
3. **Live Codex E2E**: remove `chatgpt_http` workaround from `~/.codex/config.toml`, `contextspy start`, `contextspy run codex .` with ChatGPT-plan auth, 2-3 turns in one session. Verify: one dashboard row per turn (live via broadcast), provider `openai_chatgpt`, agent `codex`, WS badge, no 101 junk rows, category breakdown + provider usage tokens populated, plausible ttft < duration.
4. Interrupt a turn mid-generation → dangling row with `ws_incomplete`.
5. HTTP regression: a Claude Code request and the Codex-over-HTTP path record as before; pre-existing DB opens cleanly (old rows read `transport='http'`).
