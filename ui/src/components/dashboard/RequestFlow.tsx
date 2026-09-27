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
import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'
import type { DashboardRequestFlowItem, DashboardConversation } from '../../api/client'
import { formatRequestDuration } from '../../lib/format'
import { parseServerTimestamp, requestLabel } from './dashboardFormat'

function statusOf(item: DashboardRequestFlowItem): { text: string; className: string } | null {
  if (item.invocation_outcome === 'failed' || (item.status_code != null && item.status_code >= 400)) {
    return { text: item.status_code != null && item.status_code >= 400 ? `Failed (${item.status_code})` : 'Failed', className: 'status-danger' }
  }
  if (item.invocation_outcome === 'incomplete') return { text: 'Incomplete', className: 'status-warning' }
  return null
}

type FlowItem = DashboardRequestFlowItem & Partial<DashboardConversation['recent_segments'][number]['request_flow'][number]>

const lineageMarker: Record<NonNullable<FlowItem['lineage_relation']>, { icon: string; label: string; uncertain: boolean }> = {
  exact: { icon: '↳', label: 'Exact predecessor', uncertain: false },
  inferred: { icon: '≈', label: 'Inferred predecessor', uncertain: false },
  suggested: { icon: '≈', label: 'Suggested predecessor', uncertain: true },
  context_affinity: { icon: '⋯', label: 'Same stream; direct predecessor not established', uncertain: true },
  ambiguous: { icon: '?', label: 'Direct predecessor ambiguous', uncertain: true },
  unresolved_exact: { icon: '!', label: 'Provider predecessor missing', uncertain: true },
  unavailable: { icon: '?', label: 'Predecessor context unavailable', uncertain: true },
  root: { icon: '○', label: 'No predecessor established', uncertain: true },
  external: { icon: '↗', label: 'Predecessor in another session', uncertain: false },
}

export function RequestFlow({ items, bare = false, trailingAction }: { items: FlowItem[]; bare?: boolean; trailingAction?: ReactNode }) {
  return (
    <div className={bare ? 'min-w-0' : 'panel min-w-0'}>
      {!bare && <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 id="request-flow-title" className="section-title">Request flow</h2>
        <span className="text-xs text-[var(--text-muted)]">Newest first</span>
      </div>}
      {items.length === 0 ? (
        <p className="py-4 text-center text-sm text-[var(--text-muted)]">No requests captured in this session yet.</p>
      ) : (
        <div className="flex min-w-0 gap-2 overflow-x-auto overscroll-x-contain pb-2" role="group" tabIndex={0} aria-label="Request sequence; scroll horizontally for older requests">
          <ol
            aria-labelledby={bare ? undefined : 'request-flow-title'}
            aria-description="Most recent requests in this session, ordered newest first"
            className="flex w-max shrink-0 gap-2"
          >
            {items.map((item) => {
            const label = requestLabel(item.session_seq, item.id)
            const time = new Date(parseServerTimestamp(item.timestamp)).toLocaleTimeString()
            const status = statusOf(item)
            const marker = item.lineage_relation ? lineageMarker[item.lineage_relation] : null
            const meta = [item.model, item.duration_ms != null ? formatRequestDuration(item.duration_ms) : null]
              .filter(Boolean)
              .join(' · ')
            return (
              <li key={item.id} className="w-36 shrink-0">
                <Link
                  to={`/requests/${item.id}`}
                  aria-label={`Request ${label}, ${time}, input ${item.tokens_total_input.toLocaleString()} tokens, output ${item.tokens_total_output.toLocaleString()} tokens${marker ? `, ${marker.label}` : ''}${status ? `, ${status.text}` : ''}${item.shared_history ? ', shared history' : ''}${item.membership_state === 'unassigned' ? ', stream membership uncertain' : ''}`}
                  className="block h-full rounded-md border border-[var(--border)] bg-[var(--surface-muted)] p-2.5 transition-colors hover:bg-[var(--surface-hover)]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-[var(--text)]">{label}</span>
                    {marker && <span aria-hidden="true" title={marker.label} className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border text-xs font-semibold ${marker.uncertain ? 'border-[var(--warning)] text-[var(--warning)]' : 'border-[var(--border)] text-[var(--accent-soft-text)]'}`}>{marker.icon}</span>}
                  </div>
                  <span className="mt-0.5 block text-xs tabular-nums text-[var(--text-muted)]">{time}</span>
                  {(meta || status) && (
                    <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                      {meta && <span className="truncate" title={meta}>{meta}</span>}
                      {status && <span className={`app-badge ${status.className}`}>{status.text}</span>}
                    </p>
                  )}
                  <p className="mt-2 text-xs tabular-nums text-[var(--text)]">
                    <span aria-hidden="true">↓ </span>{item.tokens_total_input.toLocaleString()} in
                  </p>
                  <p className="text-xs tabular-nums text-[var(--text)]">
                    <span aria-hidden="true">↑ </span>{item.tokens_total_output.toLocaleString()} out
                  </p>
                  {item.parent_state && (
                    <p className="mt-2 text-[11px] text-[var(--text-muted)]">
                      {item.lineage_relation === 'context_affinity'
                        ? 'Same stream · direct predecessor not established'
                        : item.parent_request_id
                        ? `${item.certainty === 'inferred' ? `Inferred ${Math.round((item.confidence ?? 0) * 100)}%` : 'Exact'} parent · ${item.parent_request_id.slice(0, 8)}`
                        : item.parent_state === 'root' ? 'No parent established' : `Parent ${item.parent_state.replace('_', ' ')}`}
                      {item.shared_history ? ' · Shared history' : ''}
                      {item.membership_state === 'unassigned' ? ' · Stream uncertain' : ''}
                    </p>
                  )}
                </Link>
              </li>
            )
            })}
          </ol>
          {trailingAction && <div className="w-32 shrink-0">{trailingAction}</div>}
        </div>
      )}
    </div>
  )
}
