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
import type { Request, Session } from '../api/client';
import { ContextBar } from './ContextBar';

const PROVIDER_COLORS: Record<string, string> = {
  openai: 'bg-green-900 text-green-300',
  anthropic: 'bg-orange-900 text-orange-300',
  ollama: 'bg-blue-900 text-blue-300',
  unknown: 'bg-gray-700 text-gray-400',
};

const AGENT_COLORS: Record<string, string> = {
  copilot: 'bg-purple-900 text-purple-300',
  claude: 'bg-orange-900 text-orange-300',
  cursor: 'bg-blue-900 text-blue-300',
  unknown: 'bg-gray-700 text-gray-400',
};

type SortKey =
  | 'timestamp'
  | 'tokens_total_input'
  | 'tokens_total_output'
  | 'duration_ms'
  | 'status_code'
  | 'session'
  | 'provider'
  | 'agent'
  | 'model';

function SortHeader({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  className = '',
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey | null;
  sortDir: 'asc' | 'desc';
  onSort: (col: SortKey) => void;
  className?: string;
}) {
  const active = sortKey === col;
  return (
    <th
      className={`pb-2 pr-3 font-medium cursor-pointer select-none whitespace-nowrap hover:text-gray-200 ${className}`}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && <span className="text-indigo-400">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </span>
    </th>
  );
}

function statusBadge(code: number | null) {
  if (code === null) return null;
  let cls = 'bg-gray-700 text-gray-400';
  if (code >= 200 && code < 300) cls = 'bg-green-900 text-green-300';
  else if (code >= 400 && code < 500) cls = 'bg-orange-900 text-orange-300';
  else if (code >= 500) cls = 'bg-red-900 text-red-300';
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-mono font-medium ${cls}`}>
      {code}
    </span>
  );
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface Props {
  requests: Request[];
  sessions?: Session[];
  onRowClick: (id: string) => void;
}

export function RequestTable({ requests, sessions, onRowClick }: Props) {
  const [hideEmpty, setHideEmpty] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const sessionMap = new Map((sessions ?? []).map(s => [s.id, s.name]));

  function handleSort(col: SortKey) {
    if (sortKey === col) {
      if (sortDir === 'asc') setSortDir('desc');
      else { setSortKey(null); setSortDir('asc'); }
    } else {
      setSortKey(col);
      setSortDir('asc');
    }
  }

  const filtered = hideEmpty
    ? requests.filter(r => r.tokens_total_input > 0 || r.tokens_total_output > 0)
    : requests;

  const visible = sortKey
    ? [...filtered].sort((a, b) => {
        let av: string | number | null | undefined;
        let bv: string | number | null | undefined;
        if (sortKey === 'session') {
          av = a.session_id ? (sessionMap.get(a.session_id) ?? '') : '';
          bv = b.session_id ? (sessionMap.get(b.session_id) ?? '') : '';
        } else {
          av = a[sortKey] as string | number | null;
          bv = b[sortKey] as string | number | null;
        }
        if (av == null) return sortDir === 'asc' ? 1 : -1;
        if (bv == null) return sortDir === 'asc' ? -1 : 1;
        if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
        return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number);
      })
    : filtered;

  if (requests.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500 text-sm">
        No requests captured yet.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      {/* Filter row */}
      <div className="flex items-center gap-2 mb-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideEmpty}
            onChange={e => setHideEmpty(e.target.checked)}
            className="accent-indigo-500 w-3.5 h-3.5 cursor-pointer"
          />
          Hide empty requests
        </label>
        {hideEmpty && requests.length !== visible.length && (
          <span className="text-xs text-gray-600">
            ({requests.length - visible.length} hidden)
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-700">
            <SortHeader label="Time" col="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Tokens (in)" col="tokens_total_input" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
            <SortHeader label="Tokens (out)" col="tokens_total_output" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
            <th className="pb-2 pr-3 font-medium whitespace-nowrap" style={{ minWidth: 256 }}>Context</th>
            <SortHeader label="Duration" col="duration_ms" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
            <SortHeader label="Status" col="status_code" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
            <SortHeader label="Session" col="session" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Provider" col="provider" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Agent" col="agent" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Model" col="model" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          </tr>
        </thead>
        <tbody>
          {visible.map((req) => (
            <tr
              key={req.id}
              onClick={() => onRowClick(req.id)}
              className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer transition-colors"
            >
              <td className="py-2 pr-3 text-gray-400 font-mono text-xs whitespace-nowrap">
                {formatTime(req.timestamp)}
              </td>
              <td className="py-2 pr-3 text-right text-gray-300">
                {req.tokens_total_input > 0 ? req.tokens_total_input.toLocaleString() : '—'}
              </td>
              <td className="py-2 pr-3 text-right text-gray-300">
                {req.tokens_total_output > 0 ? req.tokens_total_output.toLocaleString() : '—'}
              </td>
              <td className="py-2 pr-3">
                <ContextBar data={req} />
              </td>
              <td className="py-2 pr-3 text-right text-gray-400 whitespace-nowrap">
                {formatDuration(req.duration_ms)}
              </td>
              <td className="py-2 pr-3 text-right">
                {statusBadge(req.status_code)}
              </td>
              <td className="py-2 pr-3">
                {req.session_id && sessionMap.has(req.session_id) ? (
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-900 text-indigo-300 truncate max-w-[100px] inline-block" title={sessionMap.get(req.session_id)}>
                    {sessionMap.get(req.session_id)}
                  </span>
                ) : (
                  <span className="text-gray-600 text-xs">n/a</span>
                )}
              </td>
              <td className="py-2 pr-3">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    PROVIDER_COLORS[req.provider] ?? PROVIDER_COLORS.unknown
                  }`}
                >
                  {req.provider}
                </span>
              </td>
              <td className="py-2 pr-3">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    AGENT_COLORS[req.agent ?? 'unknown'] ?? AGENT_COLORS.unknown
                  }`}
                >
                  {req.agent}
                </span>
              </td>
              <td className="py-2 text-gray-300 truncate max-w-[120px]">
                {req.model ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
