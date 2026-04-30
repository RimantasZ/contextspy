import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStatsOverview, useTimeline, useRequests, useToolStats } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { TimeSeriesChart } from '../components/TimeSeriesChart';
import { RequestTable } from '../components/RequestTable';
import { SessionControls } from '../components/SessionControls';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';

type Bucket = 'minute' | 'hour' | 'day';

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-semibold text-white">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [bucket, setBucket] = useState<Bucket>('hour');

  const stats = useStatsOverview();
  const timeline = useTimeline(undefined, bucket);
  const requests = useRequests({ limit: 20 });
  const toolStats = useToolStats();

  const s = stats.data;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <SessionControls />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total requests" value={s?.request_count ?? '—'} />
        <StatCard label="Tokens in" value={s ? s.tokens_total_input.toLocaleString() : '—'} />
        <StatCard label="Tokens out" value={s ? s.tokens_total_output.toLocaleString() : '—'} />
        <StatCard label="Providers" value={s ? Object.keys(s.by_provider).length : '—'} />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Token composition</p>
          {s ? (
            <TokenDonut data={Object.fromEntries(Object.entries(s.by_category).map(([k, v]) => [k, v.tokens]))} />
          ) : (
            <div className="h-60 flex items-center justify-center text-gray-500 text-sm">
              {stats.isLoading ? 'Loading\u2026' : 'No data'}
            </div>
          )}
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <TimeSeriesChart
            data={timeline.data?.timeline ?? []}
            bucket={bucket}
            onBucketChange={setBucket}
            loading={timeline.isLoading}
          />
        </div>
      </div>

      {/* Tool breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ToolBreakdownCharts tools={toolStats.data?.tools ?? []} />
        <ToolBreakdownTable tools={toolStats.data?.tools ?? []} totalInputTokens={s?.tokens_total_input} />
      </div>

      {/* Recent requests */}
      <div className="bg-gray-800 rounded-lg p-4">
        <p className="text-sm font-medium text-gray-300 mb-4">Recent requests</p>
        <RequestTable
          requests={requests.data?.requests ?? []}
          onRowClick={(id) => navigate(`/requests/${id}`)}
        />
      </div>
    </div>
  );
}
