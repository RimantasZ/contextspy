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
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { TimelineBucket } from '../api/client';

type Bucket = 'minute' | 'hour' | 'day';

interface Props {
  data: TimelineBucket[];
  bucket: Bucket;
  onBucketChange: (b: Bucket) => void;
  loading?: boolean;
}

function formatLabel(ts: string, bucket: Bucket): string {
  const d = new Date(ts);
  if (bucket === 'minute') {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (bucket === 'hour') {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function TimeSeriesChart({ data, bucket, onBucketChange, loading }: Props) {
  const formatted = data.map((d) => ({
    ...d,
    label: formatLabel(d.bucket, bucket),
  }));

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-[var(--text-muted)]">Token usage over time</span>
        <div className="flex gap-1">
          {(['minute', 'hour', 'day'] as Bucket[]).map((b) => (
            <button
              key={b}
              onClick={() => onBucketChange(b)}
              className={`min-h-8 rounded px-2 py-1 text-xs ${
                bucket === b
                  ? 'bg-[var(--accent)] text-[var(--text-on-accent)]'
                  : 'bg-[var(--surface-muted)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]'
              }`}
            >
              {b}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          Loading…
        </div>
      ) : formatted.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">
          No data yet
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={formatted} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
            <XAxis
              dataKey="label"
              tick={{ fill: 'var(--chart-axis)', fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: 'var(--chart-grid)' }}
            />
            <YAxis
              tick={{ fill: 'var(--chart-axis)', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={50}
              tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v))}
            />
            <Tooltip
              formatter={(value: number) => [`${value.toLocaleString()} tokens`]}
              contentStyle={{ backgroundColor: 'var(--chart-tooltip)', border: '1px solid var(--border)', borderRadius: '6px' }}
              labelStyle={{ color: 'var(--chart-tooltip-text)' }}
              itemStyle={{ color: 'var(--text-muted)' }}
            />
            <Line
              type="monotone"
              dataKey="tokens_total_input"
              stroke="var(--chart-line)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: 'var(--chart-line)' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
