// Copyright 2026 Rimantas Zukaitis
// Licensed under the Apache License, Version 2.0
import type { LogicalRequest, Session } from '../api/client'
import type { SortKey } from './RequestTable'

function formatTime(ts: string): string {
  const value = ts.endsWith('Z') || ts.includes('+') ? ts : `${ts}Z`
  return new Date(value).toLocaleString(undefined, {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function formatDuration(ms: number | null): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function stateBadge(row: LogicalRequest) {
  const styles = row.state === 'complete'
    ? 'bg-green-900 text-green-300'
    : row.state === 'error'
      ? 'bg-red-900 text-red-300'
      : 'bg-amber-900 text-amber-300'
  return <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${styles}`}>{row.state}</span>
}

function cacheRate(row: LogicalRequest): string {
  if (row.cumulative_input_tokens == null || row.cumulative_input_tokens === 0 || row.cumulative_cached_tokens == null) return '—'
  return `${(row.cumulative_cached_tokens / row.cumulative_input_tokens * 100).toFixed(1)}%`
}

interface Props {
  requests: LogicalRequest[]
  sessions?: Session[]
  onRowClick: (id: string) => void
  sortKey?: SortKey | null
  sortDir?: 'asc' | 'desc'
  onSortChange?: (key: SortKey | null, dir: 'asc' | 'desc') => void
}

export function LogicalRequestTable({ requests, sessions, onRowClick, sortKey = null, sortDir = 'asc', onSortChange }: Props) {
  const sessionMap = new Map((sessions ?? []).map(s => [s.id, s.name]))

  function sort(col: SortKey) {
    if (!onSortChange) return
    if (sortKey !== col) onSortChange(col, 'asc')
    else if (sortDir === 'asc') onSortChange(col, 'desc')
    else onSortChange(null, 'asc')
  }

  function header(label: string, col: SortKey, className = '') {
    return (
      <th className={`pb-2 pr-3 font-medium cursor-pointer whitespace-nowrap ${className}`} onClick={() => sort(col)}>
        {label}{sortKey === col && <span className="ml-1 text-indigo-400">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </th>
    )
  }

  if (requests.length === 0) {
    return <div className="text-center py-12 text-gray-500 text-sm">No requests captured yet.</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-700">
            {header('Time', 'timestamp')}
            {header('Peak context', 'tokens_total_input', 'text-right')}
            <th className="pb-2 pr-3 font-medium text-right whitespace-nowrap">Cumulative input</th>
            {header('Output', 'tokens_total_output', 'text-right')}
            <th className="pb-2 pr-3 font-medium text-right">Calls</th>
            <th className="pb-2 pr-3 font-medium text-right">Cache</th>
            {header('Duration', 'duration_ms', 'text-right')}
            {header('Status', 'status_code')}
            {header('Session', 'session')}
            {header('Provider', 'provider')}
            {header('Agent', 'agent')}
            {header('Model', 'model')}
          </tr>
        </thead>
        <tbody>
          {requests.map(row => (
            <tr key={row.id} onClick={() => onRowClick(row.id)} className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer">
              <td className="py-2 pr-3 text-gray-400 font-mono text-xs whitespace-nowrap">{formatTime(row.started_at)}</td>
              <td className="py-2 pr-3 text-right text-gray-200">{row.peak_context_tokens?.toLocaleString() ?? '—'}</td>
              <td className="py-2 pr-3 text-right text-gray-300">{row.cumulative_input_tokens?.toLocaleString() ?? '—'}</td>
              <td className="py-2 pr-3 text-right text-gray-300">{row.cumulative_output_tokens?.toLocaleString() ?? '—'}</td>
              <td className="py-2 pr-3 text-right">
                <span className={row.invocation_count > 1 ? 'text-indigo-300 font-medium' : 'text-gray-400'}>{row.invocation_count}</span>
              </td>
              <td className="py-2 pr-3 text-right text-teal-300">{cacheRate(row)}</td>
              <td className="py-2 pr-3 text-right text-gray-400">{formatDuration(row.duration_ms)}</td>
              <td className="py-2 pr-3">{stateBadge(row)}</td>
              <td className="py-2 pr-3 text-xs text-gray-400">{row.session_id ? sessionMap.get(row.session_id) ?? 'session' : 'n/a'}</td>
              <td className="py-2 pr-3 text-gray-300">{row.provider}</td>
              <td className="py-2 pr-3 text-gray-300">{row.agent ?? '—'}</td>
              <td className="py-2 text-gray-300 truncate max-w-[140px]">{row.model ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
