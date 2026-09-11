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
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStatsOverview, useRequests, useToolStats, useSessions, useSessionsSummary } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { RequestTable } from '../components/RequestTable';
import { SessionControls } from '../components/SessionControls';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';
import { OutputSplit } from '../components/OutputSplit';
import type { SessionSummaryEntry, LatencyStats } from '../api/client';

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: ReactNode }) {
  return (
    <div className="panel">
      <p className="eyebrow mb-1">{label}</p>
      <p className="text-2xl font-semibold text-[var(--text)]">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-[var(--text-muted)]">{sub}</p>}
    </div>
  );
}

function fmtMs(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatDuration(durationMs: number | null): string {
  if (durationMs === null) return 'active';
  if (durationMs < 0) return '—';
  const totalSeconds = Math.floor(durationMs / 1000);
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
    return <p className="py-4 text-center text-sm text-[var(--text-muted)]">No sessions yet</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
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
                className={`border-b border-[var(--border)] last:border-0 ${
                  !isGap ? 'cursor-pointer transition-colors hover:bg-[var(--surface-hover)]' : ''
                }`}
                onClick={() => {
                  if (!isGap && entry.session_id) onSessionClick(entry.session_id);
                }}
              >
                <td className="py-2 pr-4">
                  {isGap ? (
                    <span className="italic text-[var(--text-muted)]">{name}</span>
                  ) : (
                    <span className={`text-[var(--text)] ${isActive ? 'font-medium' : ''}`}>
                      {name}
                      {isActive && (
                        <span className="ml-2 text-xs font-normal text-[var(--success)]">● active</span>
                      )}
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap py-2 pr-4 text-[var(--text-muted)]">
                  {formatStart(entry.started_at)}
                </td>
                <td className="whitespace-nowrap py-2 pr-4 text-[var(--text-muted)]">
                  {formatDuration(entry.duration_ms)}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums text-[var(--text)]">
                  {entry.request_count.toLocaleString()}
                </td>
                <td className="whitespace-nowrap py-2 text-right tabular-nums text-[var(--text)]">
                  {entry.tokens_in.toLocaleString()} / {entry.tokens_out.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between border-t border-[var(--border)] pt-3">
          <button
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="app-button min-h-8 py-1 text-xs"
          >
            ← Prev
          </button>
          <span className="text-xs text-[var(--text-muted)]">
            {page + 1} / {totalPages}
          </span>
          <button
            disabled={page === totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="app-button min-h-8 py-1 text-xs"
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
    return <p className="py-4 text-center text-sm text-[var(--text-muted)]">No data</p>;
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
            <span className="w-40 truncate text-xs text-[var(--text)] transition-colors group-hover:text-[var(--accent-soft-text)]" title={model}>
              {model}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--surface-muted)]">
              <div className="h-1.5 rounded-full bg-[var(--chart-line)]" style={{ width: `${pct}%` }} />
            </div>
            <span className="w-16 text-right text-xs tabular-nums text-[var(--text-muted)]">
              {count.toLocaleString()} ({pct}%)
            </span>
          </div>
        );
      })}
    </div>
  );
}

function LatencyPanel({ latency, errorCount, unknownCount }: {
  latency: LatencyStats | undefined;
  errorCount: number;
  unknownCount: number;
}) {
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
          <div key={label} className="rounded bg-[var(--surface-muted)] p-3">
            <p className="mb-0.5 text-xs text-[var(--text-muted)]">{label}</p>
            <p className="text-lg font-semibold text-[var(--text)]">{value}</p>
          </div>
        ))}
      </div>
      {(errorCount > 0 || unknownCount > 0) && (
        <div className="flex gap-3">
          {errorCount > 0 && (
            <span className="status-danger rounded px-2 py-1 text-xs">
              {errorCount} error{errorCount !== 1 ? 's' : ''}
            </span>
          )}
          {unknownCount > 0 && (
            <span className="rounded bg-[var(--surface-muted)] px-2 py-1 text-xs text-[var(--text-muted)]">
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
    <div className="page-shell">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-[var(--text)]">Overview</h1>
        <SessionControls />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Context tokens" value={s ? s.tokens_total_input.toLocaleString() : '—'} />
        <StatCard
          label="Generated tokens"
          value={s ? s.tokens_total_output.toLocaleString() : '—'}
          sub={s && <OutputSplit text={s.tokens_output_text} thinking={s.tokens_output_thinking} />}
        />
        <StatCard label="Total requests" value={s?.request_count ?? '—'} />
        <StatCard label="Providers" value={s ? Object.keys(s.by_provider).length : '—'} />
      </div>

      {/* Charts + sessions row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="panel">
          <p className="section-title mb-3">Token composition</p>
          {s ? (
            <TokenDonut data={Object.fromEntries(Object.entries(s.by_category).map(([k, v]) => [k, v.tokens]))} />
          ) : (
            <div className="flex h-60 items-center justify-center text-sm text-[var(--text-muted)]">
              {stats.isLoading ? 'Loading…' : 'No data'}
            </div>
          )}
        </div>
        <div className="panel">
          <p className="section-title mb-3">Sessions</p>
          {summary.isLoading ? (
            <div className="flex h-40 items-center justify-center text-sm text-[var(--text-muted)]">Loading…</div>
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
        <ToolBreakdownCharts tools={toolStats.data?.tools ?? []} totalInputTokens={s?.tokens_total_input} />
        <ToolBreakdownTable tools={toolStats.data?.tools ?? []} totalInputTokens={s?.tokens_total_input} />
      </div>

      {/* Model breakdown + Latency */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="panel">
          <p className="section-title mb-3">Models ({s ? Object.keys(s.by_model).length : '—'} total)</p>
          <ModelBreakdown
            byModel={s?.by_model ?? {}}
            onModelClick={(model) => navigate(`/requests?model=${encodeURIComponent(model)}`)}
          />
        </div>
        <div className="panel">
          <p className="section-title mb-3">Latency &amp; Errors</p>
          <LatencyPanel latency={s?.latency} errorCount={s?.error_count ?? 0} unknownCount={s?.unknown_status_count ?? 0} />
        </div>
      </div>

      {/* Recent requests */}
      <div className="panel">
        <p className="section-title mb-4">Recent requests</p>
        <RequestTable
          requests={requests.data?.requests ?? []}
          sessions={sessions.data?.sessions}
          onRowClick={(id) => navigate(`/requests/${id}`)}
        />
      </div>
    </div>
  );
}
