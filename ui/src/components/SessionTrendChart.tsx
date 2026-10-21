// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { SessionTrend, TrendMetric, TrendPoint, TrendSeries } from '../api/client';
import { formatDateTimeCompact, formatRequestDuration } from '../lib/format';
import { TrendCompositionChart, partColor } from './SessionTrendComposition';
import type { CompositionRow } from './SessionTrendComposition';

/** Legend entries listed before "Show all". */
const LEGEND_COLLAPSED_COUNT = 4;
/** Requests shown at once; older ones are reached by panning. */
const WINDOW_SIZE = 100;
/** Keyboard/button pan step, in requests. */
const PAN_STEP = 25;
/** Pointer movement beyond this many px is a drag, not a click on a point. */
const DRAG_THRESHOLD_PX = 4;

/** At most this many conversation lines are on initially; auxiliary is always off. */
const DEFAULT_VISIBLE_SERIES = 4;
const SERIES_COLORS = 6;

interface Props {
  data: SessionTrend | undefined;
  loading?: boolean;
  metricId: string;
  onMetricChange: (id: string) => void;
  onPointClick?: (requestId: string) => void;
}

interface Datum {
  x: number;
  y: number | null;
  point: TrendPoint;
}

function formatTrendValue(unit: TrendMetric['unit'], value: number): string {
  if (unit === 'percent') return `${value.toFixed(1)}%`;
  if (unit === 'ms') return formatRequestDuration(Math.round(value));
  if (unit === 'count') return `${Math.round(value).toLocaleString()} blocks`;
  return `${Math.round(value).toLocaleString()} tokens`;
}

function formatAxisValue(unit: TrendMetric['unit'], value: number): string {
  if (unit === 'percent') return `${value}%`;
  if (unit === 'ms') return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
}

function seriesColor(index: number): string {
  return `var(--chart-series-${(index % SERIES_COLORS) + 1})`;
}

function xOf(point: TrendPoint): number {
  return point.session_seq ?? point.ordinal;
}

function isEnabledByDefault(series: TrendSeries, index: number): boolean {
  return !series.auxiliary && index < DEFAULT_VISIBLE_SERIES;
}

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ payload: Datum; name?: string; color?: string }>;
  metric: TrendMetric;
}

function TrendTooltip({ active, payload, metric }: TooltipProps) {
  const item = payload?.[0];
  if (!active || !item || item.payload.y == null) return null;
  const { point } = item.payload;
  const partial = metric.id.startsWith('context_') && point.context_fidelity !== 'complete';
  return (
    <div
      className="rounded border border-[var(--border)] px-2 py-1.5 text-xs"
      style={{ backgroundColor: 'var(--chart-tooltip)', color: 'var(--chart-tooltip-text)' }}
    >
      <div className="font-medium" style={{ color: item.color }}>{item.name}</div>
      <div>
        #{point.session_seq ?? point.ordinal}
        {point.purpose ? ` · ${point.purpose}` : ''} · {formatDateTimeCompact(point.time)}
      </div>
      <div>
        {formatTrendValue(metric.unit, item.payload.y)}
        {metric.estimated ? ' (estimated)' : ''}
      </div>
      {partial && <div className="text-[var(--text-muted)]">partial context ({point.context_fidelity})</div>}
    </div>
  );
}

export function SessionTrendChart({
  data, loading, metricId, onMetricChange, onPointClick,
}: Props) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [showAllSeries, setShowAllSeries] = useState(false);
  // Composition metrics stack one conversation at a time: which one (null = the largest) and absolute vs share.
  const [compositionKey, setCompositionKey] = useState<string | null>(null);
  const [compositionPercent, setCompositionPercent] = useState(false);
  // First visible request, as an index into `xs`; null follows the latest requests as they arrive.
  const [panStart, setPanStart] = useState<number | null>(null);
  const drag = useRef<{ pointerX: number; startIdx: number; moved: boolean } | null>(null);
  const chartBox = useRef<HTMLDivElement>(null);
  const metrics = data?.metrics ?? [];
  const metric = metrics.find((m) => m.id === metricId) ?? metrics[0];
  const series = useMemo(() => data?.series ?? [], [data]);

  // Every request x of the session, independent of which lines are shown, so the window is stable.
  const xs = useMemo(
    () => Array.from(new Set(series.flatMap((s) => s.points.map(xOf)))).sort((a, b) => a - b),
    [series],
  );
  const maxStart = Math.max(0, xs.length - WINDOW_SIZE);
  const startIdx = panStart == null ? maxStart : Math.min(panStart, maxStart);
  const firstX = xs[startIdx];
  const lastX = xs[Math.min(startIdx + WINDOW_SIZE, xs.length) - 1];
  const pannable = xs.length > WINDOW_SIZE;

  function panTo(index: number) {
    const clamped = Math.max(0, Math.min(index, maxStart));
    setPanStart(clamped >= maxStart ? null : clamped);
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!pannable || e.button !== 0) return;
    drag.current = { pointerX: e.clientX, startIdx, moved: false };
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const state = drag.current;
    const width = chartBox.current?.clientWidth;
    if (!state || !width || e.buttons === 0) return;
    const dx = e.clientX - state.pointerX;
    if (Math.abs(dx) > DRAG_THRESHOLD_PX) state.moved = true;
    if (state.moved) panTo(state.startIdx - Math.round((dx / width) * WINDOW_SIZE));
  }

  function endDrag() {
    // Keep `moved` readable for the click that follows a drag; the next pointer-down resets it.
    if (drag.current && !drag.current.moved) drag.current = null;
  }

  function handlePointClick(requestId: string) {
    if (drag.current?.moved) return;
    onPointClick?.(requestId);
  }

  const lines = useMemo(() => {
    if (!metric) return [];
    return series.flatMap((s, index) => {
      const enabled = overrides[s.key] ?? isEnabledByDefault(s, index);
      if (!enabled) return [];
      const rows: Datum[] = s.points
        .filter((point) => xOf(point) >= firstX && xOf(point) <= lastX)
        .map((point) => ({ x: xOf(point), y: point.values[metric.id] ?? null, point }));
      return [{ series: s, color: seriesColor(index), rows }];
    });
  }, [series, overrides, metric, firstX, lastX]);

  const hasValues = lines.some((l) => l.rows.some((r) => r.y != null));

  const isComposition = metric?.kind === 'composition';
  const compositionSeries = useMemo(() => {
    const chosen = series.find((s) => s.key === compositionKey);
    if (chosen) return chosen;
    const conversations = series.filter((s) => !s.auxiliary);
    return (conversations.length ? conversations : series).reduce<TrendSeries | undefined>(
      (best, s) => (best && best.request_count >= s.request_count ? best : s), undefined);
  }, [series, compositionKey]);
  const compositionRows = useMemo<CompositionRow[]>(() => {
    if (!metric || metric.kind !== 'composition' || !compositionSeries) return [];
    return compositionSeries.points
      .filter((point) => xOf(point) >= firstX && xOf(point) <= lastX)
      .map((point) => ({ x: xOf(point), point, parts: point.parts[metric.id] ?? {} }));
  }, [compositionSeries, metric, firstX, lastX]);
  const hasComposition = compositionRows.some((row) => Object.values(row.parts).some((v) => v > 0));

  function chartWithPan(chart: ReactNode) {
    return (
      <>
        <div
          ref={chartBox}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
          style={pannable ? { cursor: 'grab', touchAction: 'pan-y' } : undefined}
        >
          {chart}
        </div>
        {pannable && (
          <div className="mt-2 flex items-center gap-2 text-xs text-[var(--text-muted)]">
            <button
              aria-label="Show earlier requests"
              onClick={() => panTo(startIdx - PAN_STEP)}
              disabled={startIdx === 0}
              className="min-h-8 rounded bg-[var(--surface-muted)] px-2 py-1 hover:bg-[var(--surface-hover)] disabled:opacity-40"
            >
              ‹
            </button>
            <input
              type="range"
              aria-label="Pan through requests"
              min={0}
              max={maxStart}
              value={startIdx}
              onChange={(e) => panTo(Number(e.target.value))}
              className="min-w-0 flex-1"
            />
            <button
              aria-label="Show later requests"
              onClick={() => panTo(startIdx + PAN_STEP)}
              disabled={panStart == null}
              className="min-h-8 rounded bg-[var(--surface-muted)] px-2 py-1 hover:bg-[var(--surface-hover)] disabled:opacity-40"
            >
              ›
            </button>
            <span className="whitespace-nowrap tabular-nums">
              #{firstX}–#{lastX} of {xs.length}
            </span>
            {panStart != null && (
              <button
                onClick={() => setPanStart(null)}
                className="min-h-8 rounded bg-[var(--accent)] px-2 py-1 text-[var(--text-on-accent)]"
              >
                Latest
              </button>
            )}
          </div>
        )}
      </>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <select
          aria-label="Trend metric"
          value={metric?.id ?? ''}
          onChange={(e) => onMetricChange(e.target.value)}
          disabled={metrics.length === 0}
          className="min-h-8 rounded border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1 text-sm text-[var(--text)]"
        >
          {metrics.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      </div>

      {isComposition && metric && compositionSeries && (
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
          {series.length > 1 && (
            <select
              aria-label="Conversation"
              value={compositionSeries.key}
              onChange={(e) => setCompositionKey(e.target.value)}
              className="min-h-8 rounded border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1 text-xs text-[var(--text)]"
            >
              {series.map((s) => (
                <option key={s.key} value={s.key}>{s.label} ({s.request_count})</option>
              ))}
            </select>
          )}
          <div className="flex gap-1" role="group" aria-label="Composition scale">
            {([[false, 'Tokens'], [true, '% of total']] as Array<[boolean, string]>).map(([percent, label]) => (
              <button
                key={label}
                onClick={() => setCompositionPercent(percent)}
                aria-pressed={compositionPercent === percent}
                className={`min-h-8 rounded px-2 py-1 ${
                  compositionPercent === percent
                    ? 'bg-[var(--accent)] text-[var(--text-on-accent)]'
                    : 'bg-[var(--surface-muted)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {metric.parts.map((part) => (
            <span key={part.id} className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: partColor(part.id) }} />
              {part.label}
            </span>
          ))}
        </div>
      )}

      {!isComposition && series.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {series.map((s, index) => {
            const enabled = overrides[s.key] ?? isEnabledByDefault(s, index);
            // Collapsed: the first few series, plus any further one that is drawn so no line is missing from the legend.
            if (!showAllSeries && index >= LEGEND_COLLAPSED_COUNT && !enabled) return null;
            return (
              <label key={s.key} className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setOverrides((prev) => ({ ...prev, [s.key]: e.target.checked }))}
                />
                <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: seriesColor(index) }} />
                {s.label} ({s.request_count})
              </label>
            );
          })}
          {series.length > LEGEND_COLLAPSED_COUNT && (
            <button
              onClick={() => setShowAllSeries((v) => !v)}
              aria-expanded={showAllSeries}
              className="text-xs text-[var(--accent)] hover:underline"
            >
              {showAllSeries ? 'Show fewer' : `Show all (${series.length})`}
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">Loading…</div>
      ) : !metric || series.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">No data yet</div>
      ) : isComposition ? (
        !hasComposition ? (
          <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
            {metric.empty_hint ?? 'No data for this metric'}
          </div>
        ) : (
          chartWithPan(
            <TrendCompositionChart
              metric={metric}
              rows={compositionRows}
              percent={compositionPercent}
              onPointClick={handlePointClick}
            />,
          )
        )
      ) : lines.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          No conversation selected
        </div>
      ) : !hasValues ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          {metric.empty_hint ?? 'No data for this metric'}
        </div>
      ) : (
        chartWithPan(
          <ResponsiveContainer width="100%" height={220}>
            <LineChart margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
              <XAxis
                type="number"
                dataKey="x"
                domain={['dataMin', 'dataMax']}
                allowDecimals={false}
                tick={{ fill: 'var(--chart-axis)', fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                tickFormatter={(v: number) => `#${v}`}
              />
              <YAxis
                tick={{ fill: 'var(--chart-axis)', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={50}
                domain={metric.unit === 'percent' ? [0, 100] : [0, 'auto']}
                tickFormatter={(v: number) => formatAxisValue(metric.unit, v)}
              />
              <Tooltip shared={false} content={<TrendTooltip metric={metric} />} />
              {lines.map(({ series: s, color, rows }) => (
                <Line
                  key={s.key}
                  name={s.label}
                  data={rows}
                  dataKey="y"
                  type="monotone"
                  stroke={color}
                  strokeWidth={2}
                  connectNulls={false}
                  isAnimationActive={false}
                  dot={(props: { cx?: number; cy?: number; payload?: Datum; index?: number }) => {
                    const { cx, cy, payload, index } = props;
                    const key = `${s.key}-${index}`;
                    if (cx == null || cy == null || payload?.y == null || rows.length > 200) return <g key={key} />;
                    return <circle key={key} cx={cx} cy={cy} r={2} fill={color} stroke={color} strokeWidth={1} />;
                  }}
                  // A render function, not an options object: Recharts calls an options object's onClick
                  // with that object itself, but calls a function with the active point's props (payload).
                  activeDot={(props: { cx?: number; cy?: number; payload?: Datum }) => {
                    const { cx, cy, payload } = props;
                    if (cx == null || cy == null || payload?.y == null) return <g />;
                    return (
                      <circle
                        cx={cx}
                        cy={cy}
                        r={5}
                        fill={color}
                        stroke={color}
                        cursor={onPointClick ? 'pointer' : undefined}
                        onClick={() => handlePointClick(payload.point.request_id)}
                      />
                    );
                  }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )
      )}
    </div>
  );
}
