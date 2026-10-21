# Session trend chart (issue #66)

Status: IMPLEMENTED, including the phase 3 metrics (see "Open items" for what is still unverified or deferred). A second chart on the session
Summary view that plots one metric per request, one line per conversation, next to the existing
*Token usage over time* chart.

## Decisions (confirmed)

- Two chart panels side by side on `SessionDetail` Summary: the existing *Token usage over time*
  (`TimeSeriesChart`, bucketed, minute/hour/day) and the new trend chart (`SessionTrendChart`). The
  *Token composition* donut moves below them.
- The trend chart's **metric selector replaces its title**.
- X axis is **request number only** (`session_seq`, falling back to the server-provided `ordinal`). A time axis
  was in the first version and was removed as messy and uninformative.
- **Windowed view:** the chart shows the latest 100 requests (`WINDOW_SIZE`). The window is over the session's
  distinct request numbers regardless of which lines are on, so toggling a conversation does not move it. Pan with a
  slider, step buttons (`PAN_STEP` = 25) or pointer drag; **Latest** returns. At the latest end the window follows
  new requests; once panned back it stays put. Window state is component-local, not in the URL.
- One line per conversation. Conversations are on by default (at most 4 initially, in graph order); auxiliary is
  **off by default**; the user toggles lines with checkboxes.
- v1 metrics (selector items):
  1. **Context size (estimated)**: `tokens_total_input` (same value dashboard and request detail show)
  2. **Cache hit %**: identical to request detail "Cached share":
     `cache_read_tokens / provider_input_tokens * 100`, null when provider input is missing/0
  3. **TTFT** (`ttft_ms`) and 4. **Latency** (`duration_ms`), in ms
- Provider-reported context size: deliberately skipped for now.
- Phase 3 metrics (added after v1): **Output tokens (estimated)** (`tokens_total_output`), **Blocks (input)** (count of
  input blocks per request, one grouped query, always computed), and two **composition** metrics: **Context
  composition** (the eight input categories) and **Output composition** (text vs thinking), drawn as stacked areas.
- Composition design: a metric has `kind` `scalar` or `composition`; a composition metric lists its `parts` (id,
  label, stacking order) and each point carries `parts[metric_id]` (tokens per part). The chart stacks **one
  conversation at a time** (selector, default the largest non-auxiliary), with a tokens / % of total toggle, the
  donut's category colours (`CATEGORY_COLORS`) and a part legend replacing the line legend. Window, panning and
  click-to-open work as for lines.
- URL params: only the metric is persisted (`?metric=`).
- Partial context: a request with `context_fidelity` other than `complete` under-reports its context size. The
  tooltip says "partial context"; there is **no marker on the line** (hollow dots were tried and dropped as too
  bulky).
- The old `/stats/timeline` endpoint, `crud.get_timeline`, `useTimeline` and `TimeSeriesChart` are **kept**
  (they were removed in the first version and restored on request).

## Problems with the previous implementation

- The old chart (kept) sums `tokens_total_input` per time bucket: that is request volume, not context size, and it
  loads full `Request` ORM rows.
- Metric hardcoded in the component; adding one meant editing API, client types and chart.
- No conversation awareness, no request-indexed axis.

## Backend

### Endpoint
`GET /sessions/{session_id}/trend` in `api/routers/sessions.py`; logic in `db/trend_service.py`, reached through
`crud.get_session_trend` (analysis stays in Python per AGENTS.md; the UI only plots).

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
     "points": [{"request_id": "...", "session_seq": 1, "ordinal": 1, "time": "...", "context_fidelity": "complete",
                 "purpose": "...",
                 "values": {"context_estimated": 1234, "cache_hit_pct": 82.5, "ttft_ms": 640, "duration_ms": 5100}}]}
  ]
}
```
- `metrics` is a registry in Python (`id`, `label`, `unit`, `estimated`, `empty_hint`, `compute(row)`); a new
  metric is one registry entry, no client change besides rendering unit formatting.
- Metric value is `null` when not computable (e.g. `provider_input_tokens` 0/None for cache %). The chart leaves a
  gap (`connectNulls=false`); a metric that is null for every plotted point shows the registry's `empty_hint`.
- `ordinal` is the 1-based position of the request in the session ordering; it is the request-number fallback when
  `session_seq` is null (legacy rows). `time` = `started_at ?? timestamp` (`timestamp` is completion time;
  `started_at` is null on historical rows); it appears in the tooltip only.
- `context_fidelity` is returned per point (see "Partial context" above).
- Ordering: `(session_seq, started_at ?? timestamp, id)`, null `session_seq` last.
- Cache hit % shares one helper, `cached_share_pct` in `analysis/accounting.py`, with `Request.to_dict`
  ("Cached share"), so they cannot drift. `crud.get_stats` session cache % also adds `cache_creation`; that
  pre-existing inconsistency is out of scope.
- `ttft_ms` is null for non-streaming responses; `duration_ms` null if the response was incomplete. Both leave gaps.
- Conversation grouping reuses `crud.get_session_lineage_graph` (`graph["conversations"]`, `graph["auxiliary"]`,
  each with ordered `request_ids`). Rules: drop ids not belonging to this session (external lineage parents); a
  request in several groups is plotted only in the first group; a session request in no group goes to the
  auxiliary series. **Series order is the graph's natural order** (Conversation 1, 2, ...), not
  `_conversation_order` (most recent first), so a conversation keeps its colour as activity moves between them.
- Query: `select(Request).options(load_only(...), raiseload=True)` filtered by `session_id` (uses
  `idx_requests_session`); no blocks or bodies are loaded.
- 404 if the session is missing. Archived sessions: same endpoint, see "Open items".

### No schema change
All fields exist on `Request`. No migration step needed.

## Frontend

- `components/SessionTrendChart.tsx`; `TimeSeriesChart.tsx` stays and sits in the left panel.
- `api/client.ts`: `SessionTrend` types, `sessionsApi.trend(id)`. `api/hooks.ts`: `useSessionTrend(id)` with query
  key `['stats', 'session-trend', id]`, so the websocket's existing invalidation of `stats` refreshes it live.
- Header row: metric `<select>` (replaces the title). Above the chart: a legend with a checkbox per conversation
  (shown when there is more than one series; first 4 plus "Show all (N)" when there are more), and the pan row (slider, step buttons, `#a-#b of N`, **Latest**) when
  the session has more than 100 requests.
- Colours: `--chart-series-1..6` tokens per theme, keyed by series index (stable across metric changes).
- Recharts `LineChart`: one `<Line>` per enabled series, each with its own dataset (series share x positions, so no
  merged table). `XAxis` is numeric on the request number.
- Tooltip: per-point hover (`shared={false}`, custom content): series label, `#seq`, purpose, time, formatted value,
  "estimated" when `estimated`, "partial context" when fidelity is not `complete`. Clicking a point opens
  `/requests/{id}` (suppressed after a drag).
- Dots: small solid dots on lines of 200 points or fewer, none above; `isAnimationActive={false}`.
- Y formatting by `unit` (`k` for tokens, `%` for percent, ms/s for time). Percent axis fixed 0-100.
- Request mode skips the request numbers used by other conversations; that is intended (shows interleaving).

## Tests

- `tests/test_session_trend.py`: values per request, null provider usage/ttft, cache % parity with
  `Request.to_dict`, `started_at` fallback, fidelity flag, external ids dropped, request in two groups plotted once,
  auxiliary split, null `session_seq` ordering and ordinal, missing session 404, block counts (input only, this
  session only), output tokens and composition, context composition parts.
- `SessionTrendChart.test.tsx`: metric list and change, unknown-metric fallback, conversation/auxiliary toggles, legend collapse, composition mode (conversation selector, scale toggle, parts legend,
  empty state, click-to-open), block count line,
  empty hint, loading/empty states, window of 100, step buttons, slider, hold position on new data, oldest limit.
- `SessionDetail*.test.tsx` mock both `useTimeline` and `useSessionTrend`.

## Docs
`docs/changelog.md` entry and the `SPEC.md` endpoint row. `README.md` and `docs/examples.md` do not mention the
chart.

## Rollout / phases
1. Backend service + endpoint + tests. Done.
2. Chart component, hook, SessionDetail layout, UI tests, `make ui`. Done.
3. Follow-ups, not started: block count (one grouped `BlockRecord` query, reuse `_block_counts`), output tokens,
   stacked categories, dashboard placement, downsampling for very large sessions.

## Verification (2026-10-21)

Measured on a scratch copy holding the metadata (no bodies) of two sessions from the author's database, one machine,
one cold process each (`crud.get_session_trend`):

| Session | Old `get_timeline` | Trend, cold | Trend, warm (graph cached) | `build_session_trend` alone | Response |
|---|---|---|---|---|---|
| 564 requests | 0.06 s | 5.1 s | 0.47 s | 0.01 s | 170 KiB |
| 4,032 requests | 0.29 s | 55 s | 2.9 s | 0.08 s | 1.2 MiB |

- After the phase 3 metrics (block count query and composition `parts`), the 4,032-request session measured: cold
  57 s, warm 4.0 s (graph cache hit and revision check 2.6 s, `build_session_trend` alone 0.41 s, was 0.08 s),
  response 2.8 MiB (was 1.2 MiB; the per-point `parts` dicts are most of the growth). If payload size matters, send
  `parts` only for the composition metric being viewed.
- The trend's own work is negligible. All of the cost is the lineage graph the endpoint needs for the conversation
  split: the cold build (`build_lineage_graph`) and, on every call, the revision check that hashes every block row.
  Both are the problems of `plans/unconfirmed_drafts/perf-cold-lineage-analysis-68.md`; the trend inherits any fix.
- Consequence: the Summary view, which never needed the lineage before, now pays it on first load, so a long
  session's Summary chart stays empty for the cold-build time (5 s / 55 s here).
- Done: the hook no longer polls every 10 s (each poll cost the full warm price, 2.9 s on the long session, even for
  an ended session); the websocket's `stats` invalidation already refreshes it on each new request.
- Archived sessions: the endpoint reads only request metadata and block ids, which an archive keeps;
  `tests/test_session_archive.py::test_session_trend_is_the_same_before_and_after` checks the response is identical
  before and after archiving.
- `?metric=` handling in `SessionDetail` is covered by `SessionDetail.test.tsx` (read from the URL, written back
  with the other parameters kept, history entry replaced, empty when absent).

## Open items
- **Cold-load cost** (above): decided to leave it until the lineage perf work (#68) lands; the trend inherits any
  fix. Long sessions show an empty chart for the cold-build time until then.
- **Legend size:** resolved. The legend lists the first 4 series plus a **Show all (N)** / **Show fewer** toggle
  (`LEGEND_COLLAPSED_COUNT`); a further series that is drawn stays in the collapsed legend so no line is missing from
  it. The 4,032-request session has 50 series (49 conversations plus auxiliary).
- Verified in a browser by the author: hover tooltip, drag-to-pan. Click-to-open was broken (Recharts calls an
  `activeDot` options object's `onClick` with that object, not the point) and is fixed by using a render function;
  not yet re-checked in a browser. Dark-theme series colours not checked.
