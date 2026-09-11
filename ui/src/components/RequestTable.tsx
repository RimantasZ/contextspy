// Copyright 2026 Rimantas Zukaitis
import { useState } from 'react'
import type { KeyboardEvent, MouseEvent } from 'react'
import type { Request, Session } from '../api/client'
import { formatRequestDuration, formatRequestTime } from '../lib/format'
import { ContextBar } from './ContextBar'

export type SortKey = 'timestamp' | 'tokens_total_input' | 'tokens_output_text' | 'tokens_output_thinking' | 'duration_ms' | 'status_code' | 'session' | 'provider' | 'agent' | 'model'

function StatusBadge({ request }: { request: Request }) {
  const success = request.invocation_outcome === 'completed' || (request.status_code != null && request.status_code >= 200 && request.status_code < 300)
  const failure = request.invocation_outcome === 'failed' || (request.status_code != null && request.status_code >= 500)
  const warning = request.invocation_outcome === 'incomplete' || (request.status_code != null && request.status_code >= 400)
  const tone = success ? 'status-success' : failure ? 'status-danger' : warning ? 'status-warning' : ''
  return <span className={`app-badge font-mono ${tone}`}>{request.status_code ?? request.invocation_outcome}</span>
}

function SortHeader({ label, col, sortKey, sortDir, onSort, className = '' }: {
  label: string; col: SortKey; sortKey: SortKey | null; sortDir: 'asc' | 'desc'; onSort: (key: SortKey) => void; className?: string
}) {
  const active = col === sortKey
  return (
    <th aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'} className={`p-2 font-medium ${className}`}>
      <button type="button" onClick={() => onSort(col)} className="inline-flex items-center gap-1 whitespace-nowrap hover:text-[var(--text)]">
        {label}{active && <span aria-hidden="true">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </button>
    </th>
  )
}

interface Props {
  requests: Request[]
  sessions?: Session[]
  onRowClick: (id: string) => void
  sortKey?: SortKey | null
  sortDir?: 'asc' | 'desc'
  onSortChange?: (key: SortKey | null, direction: 'asc' | 'desc') => void
  showSession?: boolean
}

export function RequestTable({ requests, sessions, onRowClick, sortKey: externalKey, sortDir: externalDirection, onSortChange, showSession = true }: Props) {
  const [hideEmpty, setHideEmpty] = useState(true)
  const [internalKey, setInternalKey] = useState<SortKey | null>(null)
  const [internalDirection, setInternalDirection] = useState<'asc' | 'desc'>('asc')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const sessionMap = new Map((sessions ?? []).map((session) => [session.id, session.name]))
  const controlled = onSortChange != null
  const sortKey = controlled ? (externalKey ?? null) : internalKey
  const sortDir = controlled ? (externalDirection ?? 'asc') : internalDirection

  function sort(key: SortKey) {
    let nextKey: SortKey | null = key
    let nextDirection: 'asc' | 'desc' = 'asc'
    if (sortKey === key && sortDir === 'asc') nextDirection = 'desc'
    else if (sortKey === key && sortDir === 'desc') nextKey = null
    if (controlled) onSortChange?.(nextKey, nextDirection)
    else { setInternalKey(nextKey); setInternalDirection(nextDirection) }
  }

  const filtered = hideEmpty ? requests.filter((request) => request.tokens_total_input > 0 || request.tokens_total_output > 0) : requests
  const visible = !controlled && sortKey ? [...filtered].sort((a, b) => {
    let left: string | number | null | undefined
    let right: string | number | null | undefined
    if (sortKey === 'session') { left = a.session_id ? sessionMap.get(a.session_id) : ''; right = b.session_id ? sessionMap.get(b.session_id) : '' }
    else { left = a[sortKey] as string | number | null; right = b[sortKey] as string | number | null }
    if (left == null) return 1
    if (right == null) return -1
    const result = typeof left === 'string' ? left.localeCompare(String(right)) : left - Number(right)
    return sortDir === 'asc' ? result : -result
  }) : filtered

  function toggleDetails(id: string, event?: MouseEvent) {
    event?.stopPropagation()
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function onRowKey(event: KeyboardEvent, id: string) {
    if (event.key === 'Enter') onRowClick(id)
    if (event.key === ' ') { event.preventDefault(); toggleDetails(id) }
  }

  if (requests.length === 0) return <div className="py-12 text-center text-sm text-[var(--text-muted)]">No requests captured yet.</div>

  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <input type="checkbox" checked={hideEmpty} onChange={(event) => setHideEmpty(event.target.checked)} />
          Hide empty requests
        </label>
        {hideEmpty && requests.length !== visible.length && <span className="text-xs text-[var(--text-muted)]">({requests.length - visible.length} hidden)</span>}
      </div>

      <div className="hidden min-w-0 overflow-x-auto md:block">
        <table className="w-full min-w-[720px] text-sm">
          <thead><tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
            <SortHeader label="Time" col="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={sort} />
            <SortHeader label="Input" col="tokens_total_input" sortKey={sortKey} sortDir={sortDir} onSort={sort} className="text-right" />
            <th className="w-[22%] p-2 font-medium">Context summary</th>
            <SortHeader label="Duration" col="duration_ms" sortKey={sortKey} sortDir={sortDir} onSort={sort} className="text-right" />
            <SortHeader label="Status" col="status_code" sortKey={sortKey} sortDir={sortDir} onSort={sort} className="text-right" />
            <SortHeader label="Model / source" col="model" sortKey={sortKey} sortDir={sortDir} onSort={sort} />
            <th className="w-10 p-2"><span className="sr-only">Details</span></th>
          </tr></thead>
          <tbody>
            {visible.map((request) => {
              const open = expanded.has(request.id)
              return (
                <RequestRow key={request.id} request={request} open={open} showSession={showSession} sessionName={request.session_id ? sessionMap.get(request.session_id) : undefined} onOpen={() => onRowClick(request.id)} onToggle={toggleDetails} onKey={onRowKey} />
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="space-y-2 md:hidden">
        {visible.map((request) => {
          const open = expanded.has(request.id)
          return (
            <article key={request.id} tabIndex={0} onKeyDown={(event) => onRowKey(event, request.id)} className="surface rounded-lg border border-[var(--border)] p-3">
              <div className="flex items-start justify-between gap-3">
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onRowClick(request.id)}>
                  <span className="block text-xs text-[var(--text-muted)]">{formatRequestTime(request.timestamp)}</span>
                  <strong className="mt-1 block truncate text-sm">{request.model ?? request.provider}</strong>
                </button>
                <StatusBadge request={request} />
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <div><span className="block text-[var(--text-muted)]">Input</span><strong className="tabular-nums">{request.tokens_total_input.toLocaleString()}</strong></div>
                <div><span className="block text-[var(--text-muted)]">Output</span><strong className="tabular-nums">{request.tokens_total_output.toLocaleString()}</strong></div>
                <div><span className="block text-[var(--text-muted)]">Duration</span><strong className="tabular-nums">{formatRequestDuration(request.duration_ms)}</strong></div>
              </div>
              <div className="mt-3"><ContextBar data={request} /></div>
              <button type="button" aria-expanded={open} onClick={(event) => toggleDetails(request.id, event)} className="mt-3 text-xs font-medium text-[var(--accent-soft-text)]">{open ? 'Hide details' : 'More details'}</button>
              {open && <RequestDetails request={request} showSession={showSession} sessionName={request.session_id ? sessionMap.get(request.session_id) : undefined} />}
            </article>
          )
        })}
      </div>
      {visible.length === 0 && <div className="py-8 text-center text-sm text-[var(--text-muted)]">All requests are hidden by the current filter.</div>}
    </div>
  )
}

function RequestRow({ request, open, showSession, sessionName, onOpen, onToggle, onKey }: {
  request: Request; open: boolean; showSession: boolean; sessionName?: string; onOpen: () => void
  onToggle: (id: string, event?: MouseEvent) => void; onKey: (event: KeyboardEvent, id: string) => void
}) {
  return (
    <>
      <tr tabIndex={0} onClick={onOpen} onKeyDown={(event) => onKey(event, request.id)} className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--surface-muted)]">
        <td className="whitespace-nowrap p-2 font-mono text-xs text-[var(--text-muted)]">{formatRequestTime(request.timestamp)}</td>
        <td className="p-2 text-right tabular-nums">{request.tokens_total_input > 0 ? request.tokens_total_input.toLocaleString() : '—'}</td>
        <td className="p-2"><ContextBar data={request} /></td>
        <td className="whitespace-nowrap p-2 text-right tabular-nums text-[var(--text-muted)]">{formatRequestDuration(request.duration_ms)}</td>
        <td className="p-2 text-right"><StatusBadge request={request} /></td>
        <td className="max-w-[200px] p-2"><span className="block truncate font-medium" title={request.model ?? request.provider}>{request.model ?? '—'}</span><span className="block truncate text-[10px] text-[var(--text-muted)]">{request.provider}{request.agent ? ` · ${request.agent}` : ''}</span></td>
        <td className="p-2 text-right"><button type="button" aria-label={`${open ? 'Hide' : 'Show'} request details`} aria-expanded={open} onClick={(event) => onToggle(request.id, event)} className="app-button h-8 w-8 px-0">{open ? '−' : '+'}</button></td>
      </tr>
      {open && <tr className="border-b border-[var(--border)]"><td colSpan={7} className="bg-[var(--surface-muted)] px-3 py-0"><RequestDetails request={request} showSession={showSession} sessionName={sessionName} /></td></tr>}
    </>
  )
}

function RequestDetails({ request, showSession, sessionName }: { request: Request; showSession: boolean; sessionName?: string }) {
  return (
    <dl className="grid grid-cols-2 gap-3 py-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
      <div><dt className="text-[var(--text-muted)]">Output text</dt><dd className="font-medium tabular-nums">{request.tokens_output_text.toLocaleString()}</dd></div>
      <div><dt className="text-[var(--text-muted)]">Thinking</dt><dd className="font-medium tabular-nums">{request.tokens_output_thinking.toLocaleString()}</dd></div>
      <div><dt className="text-[var(--text-muted)]">Provider</dt><dd className="font-medium">{request.provider}</dd></div>
      <div><dt className="text-[var(--text-muted)]">Agent</dt><dd className="font-medium">{request.agent ?? '—'}</dd></div>
      {showSession && <div><dt className="text-[var(--text-muted)]">Session</dt><dd className="truncate font-medium" title={sessionName}>{sessionName ?? 'n/a'}</dd></div>}
      <div><dt className="text-[var(--text-muted)]">Endpoint</dt><dd className="truncate font-medium" title={request.endpoint}>{request.endpoint}</dd></div>
    </dl>
  )
}
