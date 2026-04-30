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
        <span className="text-sm text-gray-400">Token usage over time</span>
        <div className="flex gap-1">
          {(['minute', 'hour', 'day'] as Bucket[]).map((b) => (
            <button
              key={b}
              onClick={() => onBucketChange(b)}
              className={`px-2 py-1 text-xs rounded ${
                bucket === b
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
              }`}
            >
              {b}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-40 text-gray-500 text-sm">
          Loading…
        </div>
      ) : formatted.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-gray-500 text-sm">
          No data yet
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={formatted} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="label"
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: '#374151' }}
            />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={50}
              tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v))}
            />
            <Tooltip
              formatter={(value: number) => [`${value.toLocaleString()} tokens`]}
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '6px' }}
              labelStyle={{ color: '#f9fafb' }}
              itemStyle={{ color: '#d1d5db' }}
            />
            <Line
              type="monotone"
              dataKey="total_tokens"
              stroke="#6366f1"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#6366f1' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
