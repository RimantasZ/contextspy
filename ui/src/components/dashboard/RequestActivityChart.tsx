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
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { DashboardActivityPoint } from '../../api/client'
import { formatCompactTokens, formatTokenTooltip, toActivityChartData } from './dashboardFormat'

const TICK = { fill: 'var(--chart-axis)', fontSize: 11 }
const SUMMARY =
  'Input and output token totals for the ten most recent requests; input uses the left axis and output uses the right axis.'

export function RequestActivityChart({ activity }: { activity: DashboardActivityPoint[] }) {
  const data = toActivityChartData(activity)

  return (
    <div className="panel">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="section-title">Request activity</h2>
        <ul className="flex gap-4 text-xs text-[var(--text-muted)]">
          <li className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[var(--chart-input)]" aria-hidden="true" />
            Input (left axis)
          </li>
          <li className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[var(--chart-output)]" aria-hidden="true" />
            Output (right axis)
          </li>
        </ul>
      </div>
      {data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-sm text-[var(--text-muted)]">
          No requests captured in this session yet.
        </div>
      ) : (
        <div role="img" aria-label={SUMMARY}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={{ stroke: 'var(--chart-grid)' }} />
              <YAxis
                yAxisId="input"
                orientation="left"
                tick={TICK}
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={formatCompactTokens}
                label={{ value: 'Input tokens', angle: -90, position: 'insideLeft', fill: 'var(--chart-axis)', fontSize: 11, offset: 4 }}
              />
              <YAxis
                yAxisId="output"
                orientation="right"
                tick={TICK}
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={formatCompactTokens}
                label={{ value: 'Output tokens', angle: 90, position: 'insideRight', fill: 'var(--chart-axis)', fontSize: 11, offset: 4 }}
              />
              <Tooltip
                formatter={formatTokenTooltip}
                cursor={{ fill: 'var(--surface-hover)' }}
                contentStyle={{ backgroundColor: 'var(--chart-tooltip)', border: '1px solid var(--border)', borderRadius: '6px' }}
                labelStyle={{ color: 'var(--chart-tooltip-text)' }}
                itemStyle={{ color: 'var(--text-muted)' }}
              />
              <Bar yAxisId="input" dataKey="tokens_total_input" name="Input" fill="var(--chart-input)" radius={[2, 2, 0, 0]} />
              <Bar yAxisId="output" dataKey="tokens_total_output" name="Output" fill="var(--chart-output)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
