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
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStatsOverview, useRequests, useToolStats, useSessions, useSessionsSummary } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { RequestTable } from '../components/RequestTable';
import { SessionControls } from '../components/SessionControls';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';
import type { SessionSummaryEntry, LatencyStats } from '../api/client';

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-semibold text-white">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function fmtMs(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
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

const SESSIONS_PAGE_SIZE = 5;

function SessionsTable({ entries, onSessionClick }: {
  entries: SessionSummaryEntry[];
  onSessionClick: (id: string) => void;
}) {
  const [page, setPage] = useState(0);
  const totalPages = Math.ceil(entries.length / SESSIONS_PAGE_SIZE);
  const visible = entries.slice(page * SESSIONS_PAGE_SIZE, (page + 1) * SESSIONS_PAGE_SIZE);

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
          {visible.map((entry, i) => {
            const isGap = entry.type === 'gap';
            const isActive = entry.is_active;
            const name = isGap
              ? '[No session]'
              : (entry.name ?? '(unnamed)');

            return (
              <tr
                key={entry.session_id ?? `gap-${page}-${i}`}
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
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-700">
          <button
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="px-2 py-1 text-xs bg-gray-700 text-gray-300 rounded disabled:opacity-40 hover:bg-gray-600 transition-colors"
          >
            ← Prev
          </button>
          <span className="text-xs text-gray-500">
            {page + 1} / {totalPages}
          </span>
          <button
            disabled={page === totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="px-2 py-1 text-xs bg-gray-700 text-gray-300 rounded disabled:opacity-40 hover:bg-gray-600 transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function ModelBreakdown({ byModel, onModelClick }: {
  byModel: Record<string, number>;
  onModelClick: (model: string) => void;
}) {
  const entries = Object.entries(byModel)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10);
  const total = entries.reduce((s, [, n]) => s + n, 0);

  if (entries.length === 0) {
    return <p className="text-gray-500 text-sm py-4 text-center">No data</p>;
  }

  return (
    <div className="space-y-2">
      {entries.map(([model, count]) => {
        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
        return (
          <div key={model}
            className="flex items-center gap-2 cursor-pointer group"
            onClick={() => onModelClick(model)}
          >
            <span className="text-xs text-gray-300 truncate w-40 group-hover:text-white transition-colors" title={model}>
              {model}
            </span>
            <div className="flex-1 bg-gray-700 rounded-full h-1.5 overflow-hidden">
              <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${pct}%` }} />
            </div>
            <span className="text-xs text-gray-400 w-16 text-right tabular-nums">
              {count.toLocaleString()} ({pct}%)
            </span>
          </div>
        );
      })}
    </div>
  );
}

function LatencyPanel({ latency, byStatus }: { latency: LatencyStats | undefined; byStatus: Record<string, number> | undefined }) {
  const errorCount = Object.entries(byStatus ?? {})
    .filter(([code]) => code !== 'unknown' && parseInt(code) >= 400)
    .reduce((s, [, n]) => s + n, 0);
  const unknownCount = byStatus?.['unknown'] ?? 0;

  const rows = [
    { label: 'Avg', value: fmtMs(latency?.avg_ms ?? null) },
    { label: 'P50', value: fmtMs(latency?.p50_ms ?? null) },
    { label: 'P95', value: fmtMs(latency?.p95_ms ?? null) },
    { label: 'P99', value: fmtMs(latency?.p99_ms ?? null) },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        {rows.map(({ label, value }) => (
          <div key={label} className="bg-gray-700/50 rounded p-3">
            <p className="text-xs text-gray-400 mb-0.5">{label}</p>
            <p className="text-lg font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>
      {(errorCount > 0 || unknownCount > 0) && (
        <div className="flex gap-3">
          {errorCount > 0 && (
            <span className="text-xs bg-red-900/60 text-red-300 px-2 py-1 rounded">
              {errorCount} error{errorCount !== 1 ? 's' : ''}
            </span>
          )}
          {unknownCount > 0 && (
            <span className="text-xs bg-gray-700 text-gray-400 px-2 py-1 rounded">
              {unknownCount} unknown status
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function Overview() {
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
        <h1 className="text-2xl font-bold text-white">Overview</h1>
        <SessionControls />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Context tokens" value={s ? s.tokens_total_input.toLocaleString() : '—'} />
        <StatCard label="Generated tokens" value={s ? s.tokens_total_output.toLocaleString() : '—'} />
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

      {/* Secondary stat cards: latency + errors */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Avg latency" value={fmtMs(s?.latency.avg_ms ?? null)} />
        <StatCard label="P95 latency" value={fmtMs(s?.latency.p95_ms ?? null)} />
        <StatCard
          label="Errors"
          value={s ? Object.entries(s.by_status).filter(([c]) => c !== 'unknown' && parseInt(c) >= 400).reduce((acc, [, n]) => acc + n, 0) : '—'}
          sub={s && s.request_count > 0
            ? `${Math.round((Object.entries(s.by_status).filter(([c]) => c !== 'unknown' && parseInt(c) >= 400).reduce((acc, [, n]) => acc + n, 0) / s.request_count) * 100)}% error rate`
            : undefined}
        />
        <StatCard label="Models" value={s ? Object.keys(s.by_model).length : '—'} />
      </div>

      {/* Model breakdown + Latency */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Models</p>
          <ModelBreakdown
            byModel={s?.by_model ?? {}}
            onModelClick={(model) => navigate(`/requests?model=${encodeURIComponent(model)}`)}
          />
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Latency &amp; Errors</p>
          <LatencyPanel latency={s?.latency} byStatus={s?.by_status} />
        </div>
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
