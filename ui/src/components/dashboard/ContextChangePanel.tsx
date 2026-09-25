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
import type { DashboardContextChange } from '../../api/client'
import { formatSignedTokens, requestLabel, selectBlockChangeRows } from './dashboardFormat'

function tokenGlyph(delta: number): string {
  if (delta > 0) return '▲'
  if (delta < 0) return '▼'
  return '•'
}

export function ContextChangePanel({ change }: { change: DashboardContextChange | null }) {
  if (!change) {
    return (
      <div className="panel">
        <h2 className="section-title">Context size</h2>
        <p className="py-6 text-center text-sm text-[var(--text-muted)]">No requests captured in this session yet.</p>
      </div>
    )
  }

  const hasPrevious = change.previous_request_id !== null
  const { rows, hiddenCount } = selectBlockChangeRows(change.block_changes)
  const previousLabel = hasPrevious
    ? requestLabel(change.previous_session_seq, change.previous_request_id as string)
    : null

  return (
    <div className="panel">
      <h2 className="section-title">Context size</h2>
      <p className="mb-3 text-xs text-[var(--text-muted)]">
        {hasPrevious ? `Latest request · compared with ${previousLabel}` : 'First request in session.'}
      </p>
      <p className="text-2xl font-semibold tabular-nums text-[var(--text)]">
        {change.tokens_total_input.toLocaleString()}{' '}
        <span className="text-sm font-normal text-[var(--text-muted)]">tokens</span>
      </p>
      {change.token_delta !== null && (
        <p className="mt-0.5 text-sm font-medium tabular-nums text-[var(--accent-soft-text)]">
          <span aria-hidden="true">{tokenGlyph(change.token_delta)} </span>
          {formatSignedTokens(change.token_delta)} tokens
        </p>
      )}

      {hasPrevious && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <h3 className="eyebrow mb-2">Block changes</h3>
          {change.comparison_fidelity === 'unavailable' ? (
            <p className="text-xs text-[var(--text-muted)]">Block comparison unavailable.</p>
          ) : (
            <>
              {rows.length === 0 ? (
                <p className="text-xs text-[var(--text-muted)]">No block-count changes.</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {rows.map((row) => (
                    <li key={row.blockType} className="flex items-center justify-between gap-2">
                      <span className="text-[var(--text)]">{row.label}</span>
                      <span
                        className="font-medium tabular-nums"
                        style={{ color: row.delta > 0 ? 'var(--success)' : 'var(--danger)' }}
                      >
                        {formatSignedTokens(row.delta)}
                      </span>
                    </li>
                  ))}
                  {hiddenCount > 0 && (
                    <li className="text-xs text-[var(--text-muted)]">
                      {hiddenCount} other block type{hiddenCount === 1 ? '' : 's'} changed
                    </li>
                  )}
                </ul>
              )}
              {change.comparison_fidelity === 'partial' && (
                <p className="mt-2 text-xs text-[var(--text-muted)]">Partial capture</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
