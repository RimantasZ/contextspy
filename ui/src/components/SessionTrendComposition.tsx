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
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { TrendMetric, TrendPoint } from '../api/client';
import { formatDateTimeCompact } from '../lib/format';
import { CATEGORY_COLORS } from './ContextBar';

const OTHER_PART_COLORS: Record<string, string> = {
  output_text: 'var(--category-user)',
  output_thinking: 'var(--category-prefill)',
};

/** Colour of a composition part: context categories share the donut's colours. */
export function partColor(id: string): string {
  return CATEGORY_COLORS[id] ?? OTHER_PART_COLORS[id] ?? 'var(--category-other)';
}

export interface CompositionRow {
  x: number;
  point: TrendPoint;
  /** Tokens per part id. */
  parts: Record<string, number>;
}

interface Props {
  metric: TrendMetric;
  rows: CompositionRow[];
  /** Fill the full height and show each part's share instead of its tokens. */
  percent: boolean;
  onPointClick: (requestId: string) => void;
}

interface TooltipProps {
  active?: boolean;
  payload?: Array<{ payload: Record<string, unknown> }>;
  metric: TrendMetric;
}

function CompositionTooltip({ active, payload, metric }: TooltipProps) {
  const row = payload?.[0]?.payload as (Record<string, number> & { __point: TrendPoint }) | undefined;
  if (!active || !row) return null;
  const point = row.__point;
  const total = metric.parts.reduce((sum, part) => sum + (row[part.id] ?? 0), 0);
  const partial = metric.id.startsWith('context_') && point.context_fidelity !== 'complete';
  return (
    <div
      className="rounded border border-[var(--border)] px-2 py-1.5 text-xs"
      style={{ backgroundColor: 'var(--chart-tooltip)', color: 'var(--chart-tooltip-text)' }}
    >
      <div>
        #{point.session_seq ?? point.ordinal}
        {point.purpose ? ` · ${point.purpose}` : ''} · {formatDateTimeCompact(point.time)}
      </div>
      {[...metric.parts].reverse().filter((part) => (row[part.id] ?? 0) > 0).map((part) => (
        <div key={part.id} className="flex justify-between gap-4">
          <span style={{ color: partColor(part.id) }}>{part.label}</span>
          <span className="tabular-nums">
            {(row[part.id] ?? 0).toLocaleString()} ({total > 0 ? (((row[part.id] ?? 0) / total) * 100).toFixed(1) : '0.0'}%)
          </span>
        </div>
      ))}
      <div className="mt-0.5 border-t border-[var(--border)] pt-0.5 font-medium">
        {total.toLocaleString()} tokens{metric.estimated ? ' (estimated)' : ''}
      </div>
      {partial && <div className="text-[var(--text-muted)]">partial context ({point.context_fidelity})</div>}
    </div>
  );
}

/** Stacked area of one conversation's per-request token composition. */
export function TrendCompositionChart({ metric, rows, percent, onPointClick }: Props) {
  const data = rows.map((row) => ({ x: row.x, request_id: row.point.request_id, __point: row.point, ...row.parts }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart
        data={data}
        stackOffset={percent ? 'expand' : 'none'}
        margin={{ top: 4, right: 8, bottom: 4, left: 8 }}
        onClick={(state) => {
          const row = state?.activePayload?.[0]?.payload as { request_id?: string } | undefined;
          if (row?.request_id) onPointClick(row.request_id);
        }}
      >
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
          domain={percent ? [0, 1] : [0, 'auto']}
          tickFormatter={(v: number) =>
            percent ? `${Math.round(v * 100)}%` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)}
        />
        <Tooltip content={<CompositionTooltip metric={metric} />} />
        {metric.parts.map((part) => (
          <Area
            key={part.id}
            dataKey={part.id}
            name={part.label}
            stackId="composition"
            type="monotone"
            stroke={partColor(part.id)}
            fill={partColor(part.id)}
            fillOpacity={0.85}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
