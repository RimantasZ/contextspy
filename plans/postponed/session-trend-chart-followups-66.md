# Session trend chart (issue #66): what was done, what is postponed

Status: **POSTPONED follow-ups.** The chart itself is implemented and committed; this file is the hand-off for coming
back to it. The full design, decisions and measurements are in
[../session-trend-chart-66.md](../session-trend-chart-66.md) (move that file to `plans/archive/` once this work is
closed). Written 2026-10-22.

## What was done

A second panel on the session Summary view, next to the kept *Token usage over time* chart (the *Token composition*
donut moved below them). It plots one metric per request, by request number, one line per conversation.

- **Backend:** `GET /api/sessions/{id}/trend` (`api/routers/sessions.py`), logic in `db/trend_service.py` via
  `crud.get_session_trend`. A Python metric registry (`id`, `label`, `unit`, `estimated`, `empty_hint`, `kind`,
  `parts`); series come from the lineage graph (conversations plus auxiliary; ids from other sessions dropped; a
  request is plotted once). Shared helper `analysis/accounting.py: cached_share_pct` is used by `Request.to_dict` too.
  No schema change.
- **Scalar metrics:** Context size (estimated), Cache hit % (same as request detail "Cached share"), Output tokens
  (estimated), Blocks (input), TTFT, Latency.
- **Composition metrics (stacked areas, one conversation at a time):** Context composition (eight categories, donut
  colours) and Output composition (text vs thinking). Tokens / % of total toggle; conversation selector.
- **Frontend:** `components/SessionTrendChart.tsx` and `SessionTrendComposition.tsx`; hook `useSessionTrend`
  (`['stats', 'session-trend', id]`, no polling, refreshed by the websocket's `stats` invalidation); metric kept in
  `?metric=`.
- **Behaviour:** latest 100 requests shown (`WINDOW_SIZE`), pan by slider, step buttons (`PAN_STEP` = 25) or drag,
  **Latest** returns and follows new requests; legend shows the first 4 series plus **Show all (N)**
  (`LEGEND_COLLAPSED_COUNT`); auxiliary off by default; at most 4 conversation lines on initially; click a point to
  open the request; tooltip flags partial context (no marker on the line).
- **Decisions along the way:** time axis dropped (messy); hollow partial-context dots dropped (too bulky); old
  chart and `/stats/timeline` kept; provider-reported context size skipped (estimated only); series in graph order, not
  most-recent-first, so colours stay stable.
- **Also fixed:** the flaky `tests/test_backups.py` restore test, caused by `list_backups` ordering a restore made in
  second `:00` before the migration backup of the same minute (`db/backups.py` sort key).
- **Tests:** `tests/test_session_trend.py`, `tests/test_session_archive.py::test_session_trend_is_the_same_before_and_after`,
  `ui/src/components/SessionTrendChart.test.tsx`, `ui/src/pages/SessionDetail.test.tsx` (metric in the URL).

## Postponed

### 1. Cold-load cost (blocked on #68)
The endpoint needs the lineage graph for the conversation split, so the Summary view now pays it on first load.
Measured 2026-10-21 on a scratch copy (metadata only, two sessions, one machine):

| Session | Old `get_timeline` | Trend cold | Trend warm | `build_session_trend` alone | Response |
|---|---|---|---|---|---|
| 564 requests | 0.06 s | 5.1 s | 0.47 s | 0.01 s | 170 KiB |
| 4,032 requests | 0.29 s | 57 s | 4.0 s | 0.41 s | 2.8 MiB |

The trend's own work is small; the cost is the cold `build_lineage_graph` and the per-call revision check that hashes
every block row ([../perf-cold-lineage-analysis-68.md](../perf-cold-lineage-analysis-68.md)).
Any fix there carries over. **Decision (2026-10-21): leave it until #68 is solved.** Until then a long session's
Summary chart is empty for the cold-build time.

If #68 stalls, the option I recommended: return a single "All requests" series immediately (no lineage, about 0.1 s)
and add the conversation split once the graph is ready (endpoint flag or two calls), so first paint does not wait.
Revisit after #68; re-measure with the steps below.

### 2. Browser verification (not done)
Checked by the author: hover tooltip, drag-to-pan on the line chart. **Not yet checked:**
- click-to-open on the line chart after the fix (Recharts calls an `activeDot` options object's `onClick` with that
  object, not the point; fixed by using a render function; covered by a jsdom test only);
- the stacked composition views (bands, conversation selector, tokens / % toggle, tooltip);
- the Blocks and Output lines, and the "Show all (N)" legend;
- dark-theme series colours (`--chart-series-1..6`) and band colours.

Quick check: open a session with more than 100 requests; hover several points; click one (should open the request);
drag right and left (label `#a-#b of N` changes, no request opens); **Latest**; toggle a conversation (window must not
move); switch to a composition metric and back.

### 3. Not started
- **Dashboard placement** of the trend chart (the active-session dashboard).
- **Downsampling** for very large sessions: today there is no point cap; dots are hidden above 200 points per line.
  Consider thinning (for example min/max per bucket) only if big sessions are slow in the browser.
- **Payload size:** the per-point `parts` dicts grew the 4,032-request response from 1.2 to 2.8 MiB. If it matters,
  send `parts` only for the composition metric being viewed (backend change plus a refetch when switching to one).

## Further considerations

- **Cache hit % inconsistency (pre-existing, out of scope):** the session-level cache % in `crud.get_stats` adds
  `cache_creation_tokens`, the per-request "Cached share" (and the trend) uses `cache_read_tokens` only. Decide
  which definition is right, then align them.
- **Provider-reported context size** was skipped on purpose. If added, note that Anthropic's `input_tokens` excludes
  cached tokens, so the full window is input + cache read + cache creation; check how each adapter stores
  `provider_input_tokens` first.
- **Partial context:** requests with `context_fidelity` other than `complete` under-report context size; only the
  tooltip says so. If dips are misread as compaction, reconsider a subtle marker (the hollow-dot version was too
  heavy).
- **Window size and step** (`WINDOW_SIZE` = 100, `PAN_STEP` = 25) were chosen without user data; tune after real use.
  Window state is component-local, not in the URL.
- **More metrics are one registry entry each** (`METRICS` in `db/trend_service.py`), plus unit formatting in the
  chart if the unit is new. Ideas not built: input blocks by type, output blocks, tool-call counts, cost estimates.
- **Memory/CPU on active sessions:** every new request refetches the trend (websocket invalidation), which costs the
  warm price above on a long session. If an active long session feels sluggish, debounce the refetch or add a cheaper
  revision check (part of #68).

## How to measure again (never the live database)

Copy only what you need from the live DB, read-only, into a scratch file: create the schema with
`Base.metadata.create_all`, `ATTACH` the live file with `file:...?mode=ro` (open the connection with `uri=True`),
and `INSERT ... SELECT` the `sessions`, `requests` (body columns as `NULL`) and `blocks` rows of the chosen sessions
(the two sessions measured had 4,032 and 564 requests, 916k block rows together). Then, in a fresh process per
session, time `crud.get_session_trend(db, session_id)` cold, then `crud.get_session_lineage_graph` and
`trend_service.build_session_trend` warm. macOS has no `timeout`: run long probes in the background and poll a log.

## Reopen checklist
1. Is #68 solved? Re-measure cold and warm; update the table above and decide whether the "All requests first" option
   is still needed.
2. Do the browser checks in section 2 and record the result in [../session-trend-chart-66.md](../session-trend-chart-66.md).
3. Pick from section 3 and the considerations.
4. When closed: move both plan files to `plans/archive/`; close issue #66 (only when asked).
