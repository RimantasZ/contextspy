# Session trend chart (issue #66)

Status: REVIEWED, ready for implementation. Replaces the "Token usage over time" chart in the
session Summary view with a metric-selectable trend chart (per request or over time,
one line per conversation).

## Decisions (confirmed)

- Chart replaces `TimeSeriesChart` in `SessionDetail` Summary. The metric **selector replaces the
  static "Token usage over time" title**.
- X axis: **Request #** (`session_seq`) or **Time** (`timestamp`). Raw points, **no bucketing**
  (minute/hour/day picker is dropped for this chart).
- One line per conversation. Primary conversation on; auxiliary **off by default**; user toggles lines.
- v1 metrics (selector items):
  1. **Context size (estimated)**: `tokens_total_input` (same value dashboard and request detail show)
  2. **Cache hit %**: identical to request detail "Cached share":
     `cache_read_tokens / provider_input_tokens * 100`, null when provider input is missing/0
  3. **TTFT** (`ttft_ms`) and 4. **Latency** (`duration_ms`), in ms
- Provider-reported context size: deliberately skipped for now.
- Later (not v1): block count, output tokens, per-category stacked.
- URL params: metric and axis persisted (`?metric=`, `?x=request|time`).
- Review decisions: mark non-complete context points; `started_at ?? timestamp` for time axis; group
  membership rules below; trend query refreshes via websocket; old `/stats/timeline` is deleted.

## Problems with the current implementation

- `crud.get_timeline` loads full `Request` ORM rows and buckets in Python; returns only
  `{bucket, request_count, tokens_total_input}`: a *sum* per bucket, which is request volume, not
  context size.
- Metric is hardcoded in the component; adding one means editing API, client types and chart.
- No conversation awareness, no request-indexed axis.

## Backend

### Endpoint
`GET /sessions/{session_id}/trend` in `api/routers/sessions.py`; logic in new
`db/trend_service.py` (analysis stays in Python per AGENTS.md; UI only plots).

Response:
```json
{
  "session_id": "...",
    "metrics": [
    {"id": "context_estimated", "label": "Context size (estimated)", "unit": "tokens", "estimated": true, "empty_hint": null},
    {"id": "cache_hit_pct",     "label": "Cache hit %", "unit": "percent", "estimated": false, "empty_hint": "Provider did not report cache usage"},
    {"id": "ttft_ms",           "label": "TTFT", "unit": "ms", "estimated": false, "empty_hint": "No streamed responses (TTFT needs streaming)"},
    {"id": "duration_ms",       "label": "Latency", "unit": "ms", "estimated": false, "empty_hint": "No completed responses"}
  ],
  "series": [
    {"key": "session:..:primary", "label": "Conversation 1", "auxiliary": false, "request_count": 42,
     "points": [{"request_id": "...", "session_seq": 1, "time": "...", "context_fidelity": "complete", "purpose": "...",
                 "values": {"context_estimated": 1234, "cache_hit_pct": 82.5, "ttft_ms": 640, "duration_ms": 5100}}]}
  ]
}
```
- `metrics` is a registry in Python (`id`, `label`, `unit`, `estimated`, `empty_hint`, `compute(row)`); a new metric is
  one registry entry, no client change besides rendering unit formatting.
- Metric value is `null` when not computable (e.g. `provider_input_tokens` 0/None for cache %).
  Chart leaves a gap (`connectNulls=false`); a metric that is null for every enabled point shows the
  registry's `empty_hint`, not a flat line.
- `time` = `started_at ?? timestamp` (`timestamp` is completion time; `started_at` is null on
  historical rows). Load `started_at` and `context_fidelity` in the query too.
- `context_fidelity` is returned per point: `partial`/`opaque` requests (e.g. stateful Responses
  calls carrying only a delta) under-report `tokens_total_input`; the chart draws them as hollow dots
  and the tooltip says "partial context" so false drops are not read as real compaction.
- Ordering: `(session_seq, time)`; `session_seq` is nullable on legacy rows, in which case Request
  mode falls back to ordinal position within the sorted session.
- Cache hit % must be the same as request detail. Today that formula is inline in
  `Request.to_dict` (`db/models.py` ~l.232, `cached_share`). Extract a small shared helper
  (`cached_share_pct(cache_read, provider_input)`) used by both `to_dict` and the trend service so
  they cannot drift. Note `crud.get_stats` session cache % also adds `cache_creation`; that is a
  pre-existing inconsistency, out of scope here (could be noted in the changelog/roadmap).
- `ttft_ms` is null for non-streaming responses; `duration_ms` null if the response was incomplete.
  Both leave gaps. Unit `ms` formats as `640 ms` / `5.1 s` in the UI.
- Conversation grouping reuses `crud.get_session_lineage_graph` (`graph["conversations"]`,
  `graph["auxiliary"]`, each with ordered `request_ids`). Rules: drop ids not belonging to this
  session (external lineage parents); a request in several groups is plotted only in the first group
  in order; a session request in no group goes to the auxiliary series. Series order = `_conversation_order(graph)`.
- Query: `select(Request).options(load_only(id, session_seq, timestamp, purpose,
  tokens_total_input, provider_input_tokens, cache_read_tokens, ttft_ms, duration_ms,
  started_at, context_fidelity), raiseload=True)`
  filtered by `session_id` (use `idx_requests_session`). No blocks/bodies loaded.
- Lineage analysis is cached by revision (`session_lineage_service`); a cold session pays its cost
  once. Check perf against the existing `perf-cold-lineage-analysis-68` draft before shipping.
- 404 if session is missing. Archived sessions: same behaviour as the other session endpoints.
- Delete `GET /stats/timeline`, `crud.get_timeline` and its tests; only this chart used them.
  Frontend: remove `useTimeline`, `statsApi.timeline`, `TimelineBucket`, and test mocks.

### No schema change
All fields exist on `Request`. No migration step needed.

## Frontend

- New `components/SessionTrendChart.tsx`; delete `TimeSeriesChart.tsx` (`knip` would flag it).
- `api/client.ts`: `TrendResponse`, `sessionsApi.trend(id)`. `api/hooks.ts`: `useSessionTrend(id)` with query key `['stats', 'session-trend', id]`, so the existing websocket
  invalidation of `stats` refreshes it live (no extra wiring).
- Header row: metric `<select>` (left, replaces title); X axis toggle `Request # | Time` (right).
- Series legend with checkbox per conversation, color per series from a fixed palette keyed by
  series index (stable across metric/axis changes). Aux off by default. Cap initially-enabled series
  at 4 (primary first), remaining off.
- Recharts `LineChart`: one `<Line>` per enabled series. Request mode: `XAxis` type number on
  `session_seq`. Time mode: numeric epoch axis (not categorical labels), tick formatter via
  `normalizeServerTimestamp`.
- Different series share X positions in Request mode, so each series is its own dataset
  (`<Line data=...>`), not a merged table.
- Tooltip: per-point hover (`shared={false}`, custom content; per-series datasets break the shared
  tooltip): series label, `#seq`, purpose, formatted value, "estimated" suffix when `estimated`,
  "partial context" when fidelity is not `complete`.
- Request mode: lines for a conversation skip the seq numbers used by other conversations; that is
  intended (shows interleaving).
- Scale: no point cap in v1; use `dot` only for non-complete points, `isAnimationActive={false}`.
  Downsampling is a follow-up if very large sessions are slow.
  Click on a point navigates to `/requests/{id}`.
- Y formatting by `unit` (`k` suffix for tokens, `%` for percent, ms/s for time). Percent axis fixed 0-100.
- Metric and axis live in URL search params (`metric`, `x`), invalid values fall back to defaults;
  toggled series stay in component state.
- Theme via existing `--chart-*` tokens. Tests: Vitest for metric switch, axis switch, aux toggle,
  null-gap empty state; update `SessionDetail*.test.tsx` mocks (`useTimeline` -> `useSessionTrend`).

## Backend tests
`Request.to_dict` `cached_share_pct` must be unchanged by the helper extraction (existing request tests + a parity test).
`tests/test_session_trend.py`: estimated context values, cache % parity with `Request.to_dict`, null provider usage/ttft, `started_at` fallback, partial-fidelity flag, external ids dropped, request in two groups plotted once, null `session_seq` ordering, `get_timeline` removal,
conversation split with auxiliary, ordering by `session_seq`, missing session 404.

## Docs
`docs/changelog.md` entry; update `docs/examples.md` (describes the old chart) and README if it mentions it.

## Rollout / phases
1. Backend service + endpoint + tests.
2. Chart component, hook, SessionDetail swap, UI tests, `make ui`.
3. Follow-ups: block count (one grouped `BlockRecord` query, reuse `_block_counts`),
   output tokens, stacked categories, dashboard placement.

## Open questions

None blocking. Resolved: cache % = request detail formula; estimated context only; metric/axis in
URL; latency and TTFT included in v1.
