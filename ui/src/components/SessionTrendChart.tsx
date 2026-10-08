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
import { useMemo, useState } from 'react';
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
import { formatDateTimeCompact, formatRequestDuration, normalizeServerTimestamp } from '../lib/format';

export type TrendXMode = 'request' | 'time';

/** At most this many conversation lines are on initially; auxiliary is always off. */
const DEFAULT_VISIBLE_SERIES = 4;
const SERIES_COLORS = 6;

interface Props {
  data: SessionTrend | undefined;
  loading?: boolean;
  metricId: string;
  onMetricChange: (id: string) => void;
  xMode: TrendXMode;
  onXModeChange: (mode: TrendXMode) => void;
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

function xOf(point: TrendPoint, mode: TrendXMode): number {
  return mode === 'time'
    ? new Date(normalizeServerTimestamp(point.time)).getTime()
    : point.session_seq ?? point.ordinal;
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
  const partial = point.context_fidelity !== 'complete';
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
  data, loading, metricId, onMetricChange, xMode, onXModeChange, onPointClick,
}: Props) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const metrics = data?.metrics ?? [];
  const metric = metrics.find((m) => m.id === metricId) ?? metrics[0];
  const series = useMemo(() => data?.series ?? [], [data]);

  const lines = useMemo(() => {
    if (!metric) return [];
    return series.flatMap((s, index) => {
      const enabled = overrides[s.key] ?? isEnabledByDefault(s, index);
      if (!enabled) return [];
      const rows: Datum[] = s.points.map((point) => ({
        x: xOf(point, xMode), y: point.values[metric.id] ?? null, point,
      }));
      return [{ series: s, color: seriesColor(index), rows }];
    });
  }, [series, overrides, metric, xMode]);

  const hasValues = lines.some((l) => l.rows.some((r) => r.y != null));

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
        <div className="flex gap-1" role="group" aria-label="X axis">
          {([['request', 'Request #'], ['time', 'Time']] as Array<[TrendXMode, string]>).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => onXModeChange(mode)}
              aria-pressed={xMode === mode}
              className={`min-h-8 rounded px-2 py-1 text-xs ${
                xMode === mode
                  ? 'bg-[var(--accent)] text-[var(--text-on-accent)]'
                  : 'bg-[var(--surface-muted)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {series.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          {series.map((s, index) => (
            <label key={s.key} className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
              <input
                type="checkbox"
                checked={overrides[s.key] ?? isEnabledByDefault(s, index)}
                onChange={(e) => setOverrides((prev) => ({ ...prev, [s.key]: e.target.checked }))}
              />
              <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: seriesColor(index) }} />
              {s.label} ({s.request_count})
            </label>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">Loading…</div>
      ) : !metric || series.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">No data yet</div>
      ) : lines.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          No conversation selected
        </div>
      ) : !hasValues ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          {metric.empty_hint ?? 'No data for this metric'}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
            <XAxis
              type="number"
              dataKey="x"
              domain={['dataMin', 'dataMax']}
              scale={xMode === 'time' ? 'time' : 'linear'}
              allowDecimals={false}
              tick={{ fill: 'var(--chart-axis)', fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: 'var(--chart-grid)' }}
              tickFormatter={(v: number) =>
                xMode === 'time'
                  ? new Date(v).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
                  : `#${v}`}
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
                  if (cx == null || cy == null || payload?.y == null) return <g key={key} />;
                  const partial = payload.point.context_fidelity !== 'complete';
                  if (!partial && rows.length > 200) return <g key={key} />;
                  return (
                    <circle
                      key={key}
                      cx={cx}
                      cy={cy}
                      r={partial ? 4 : 2}
                      fill={partial ? 'var(--chart-tooltip)' : color}
                      stroke={color}
                      strokeWidth={partial ? 2 : 1}
                    />
                  );
                }}
                activeDot={{
                  r: 5,
                  fill: color,
                  cursor: onPointClick ? 'pointer' : undefined,
                  // Recharts passes the active dot's props, including the datum, but types them as DotProps.
                  onClick: (dot: object) => {
                    const id = (dot as { payload?: Datum }).payload?.point.request_id;
                    if (id && onPointClick) onPointClick(id);
                  },
                }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
