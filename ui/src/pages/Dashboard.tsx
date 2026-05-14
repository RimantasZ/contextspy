import { useNavigate } from 'react-router-dom';
import { useStatsOverview, useRequests, useToolStats, useSessions, useSessionsSummary } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { RequestTable } from '../components/RequestTable';
import { SessionControls } from '../components/SessionControls';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';
import type { SessionSummaryEntry } from '../api/client';

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

function formatDuration(startedAt: string, endedAt: string | null): string {
  if (!endedAt) return 'active';
  const diffMs = new Date(endedAt).getTime() - new Date(startedAt).getTime();
  if (diffMs < 0) return '—';
  const totalSeconds = Math.floor(diffMs / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatStart(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function SessionsTable({ entries, onSessionClick }: {
  entries: SessionSummaryEntry[];
  onSessionClick: (id: string) => void;
}) {
  if (entries.length === 0) {
    return <p className="text-gray-500 text-sm py-4 text-center">No sessions yet</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-400 uppercase tracking-wide border-b border-gray-700">
            <th className="pb-2 pr-4 font-medium">Name</th>
            <th className="pb-2 pr-4 font-medium">Start</th>
            <th className="pb-2 pr-4 font-medium">Duration</th>
            <th className="pb-2 pr-4 font-medium text-right">Requests</th>
            <th className="pb-2 font-medium text-right">Tokens (in / out)</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => {
            const isGap = entry.type === 'gap';
            const isActive = entry.is_active;
            const name = isGap
              ? '[No session]'
              : (entry.name ?? '(unnamed)');

            return (
              <tr
                key={entry.session_id ?? `gap-${i}`}
                className={`border-b border-gray-700/50 last:border-0 ${
                  !isGap ? 'cursor-pointer hover:bg-gray-700/40 transition-colors' : ''
                }`}
                onClick={() => {
                  if (!isGap && entry.session_id) onSessionClick(entry.session_id);
                }}
              >
                <td className="py-2 pr-4">
                  {isGap ? (
                    <span className="text-gray-500 italic">{name}</span>
                  ) : (
                    <span className={`text-white ${isActive ? 'font-medium' : ''}`}>
                      {name}
                      {isActive && (
                        <span className="ml-2 text-xs text-green-400 font-normal">● active</span>
                      )}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 text-gray-400 whitespace-nowrap">
                  {formatStart(entry.started_at)}
                </td>
                <td className="py-2 pr-4 text-gray-400 whitespace-nowrap">
                  {formatDuration(entry.started_at, entry.ended_at)}
                </td>
                <td className="py-2 pr-4 text-gray-300 text-right tabular-nums">
                  {entry.request_count.toLocaleString()}
                </td>
                <td className="py-2 text-gray-300 text-right tabular-nums whitespace-nowrap">
                  {entry.tokens_in.toLocaleString()} / {entry.tokens_out.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();

  const stats = useStatsOverview();
  const requests = useRequests({ limit: 20 });
  const toolStats = useToolStats();
  const sessions = useSessions();
  const summary = useSessionsSummary();

  const s = stats.data;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <SessionControls />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Tokens in" value={s ? s.tokens_total_input.toLocaleString() : '—'} />
        <StatCard label="Tokens out" value={s ? s.tokens_total_output.toLocaleString() : '—'} />
        <StatCard label="Total requests" value={s?.request_count ?? '—'} />
        <StatCard label="Providers" value={s ? Object.keys(s.by_provider).length : '—'} />
      </div>

      {/* Charts + sessions row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Token composition</p>
          {s ? (
            <TokenDonut data={Object.fromEntries(Object.entries(s.by_category).map(([k, v]) => [k, v.tokens]))} />
          ) : (
            <div className="h-60 flex items-center justify-center text-gray-500 text-sm">
              {stats.isLoading ? 'Loading…' : 'No data'}
            </div>
          )}
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Sessions</p>
          {summary.isLoading ? (
            <div className="h-40 flex items-center justify-center text-gray-500 text-sm">Loading…</div>
          ) : (
            <SessionsTable
              entries={summary.data?.entries ?? []}
              onSessionClick={(id) => navigate(`/sessions/${id}`)}
            />
          )}
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
          sessions={sessions.data?.sessions}
          onRowClick={(id) => navigate(`/requests/${id}`)}
        />
      </div>
    </div>
  );
}
