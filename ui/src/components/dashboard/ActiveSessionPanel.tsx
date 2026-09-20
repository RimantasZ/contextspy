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
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { DashboardActiveSession } from '../../api/client'
import { useEndSession } from '../../api/hooks'
import { formatElapsedDuration } from '../../lib/format'
import { StartSessionDialog } from '../StartSessionDialog'
import { parseServerTimestamp } from './dashboardFormat'
import { useNow } from './useNow'

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="text-lg font-semibold tabular-nums text-[var(--text)]">{value}</p>
    </div>
  )
}

function ActiveDetails({ session }: { session: DashboardActiveSession }) {
  const endSession = useEndSession()
  const startedMs = parseServerTimestamp(session.started_at)
  const now = useNow(startedMs)

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <p className="eyebrow flex items-center gap-1.5 text-[var(--success)]">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--success)]" aria-hidden="true" />
          Active session
        </p>
        <Link
          to={`/sessions/${session.id}`}
          title={session.name}
          className="block max-w-[28rem] truncate text-lg font-semibold text-[var(--text)] hover:text-[var(--accent-soft-text)]"
        >
          {session.name}
        </Link>
      </div>
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <Metric label="Elapsed" value={formatElapsedDuration(now - startedMs)} />
        <Metric label="Requests" value={session.request_count.toLocaleString()} />
        <Metric label="Input tokens" value={session.tokens_total_input.toLocaleString()} />
        <Metric label="Output tokens" value={session.tokens_total_output.toLocaleString()} />
      </div>
      <div className="flex flex-col items-end gap-1">
        <button
          onClick={() => endSession.mutate(session.id)}
          disabled={endSession.isPending}
          className="app-button-danger min-h-8 py-1 text-xs"
        >
          End session
        </button>
        <p role="status" aria-live="polite" className="text-xs text-[var(--text-muted)]">
          {endSession.isPending ? 'Ending session…' : ''}
        </p>
        {endSession.isError && (
          <p role="alert" className="text-xs text-[var(--danger)]">Could not end the session.</p>
        )}
      </div>
    </div>
  )
}

export function ActiveSessionPanel({ session }: { session: DashboardActiveSession | null }) {
  const [showDialog, setShowDialog] = useState(false)

  return (
    <div className="panel">
      {session ? (
        <ActiveDetails session={session} />
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-semibold text-[var(--text)]">No active session</p>
            <p className="text-sm text-[var(--text-muted)]">
              A session groups the live requests you capture so you can follow them here.
            </p>
          </div>
          <button onClick={() => setShowDialog(true)} className="app-button-primary min-h-8 py-1 text-xs">
            Start session
          </button>
        </div>
      )}
      {showDialog && <StartSessionDialog onClose={() => setShowDialog(false)} />}
    </div>
  )
}
