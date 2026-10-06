import { useState } from 'react'
import type { OccurrenceEntry, OccurrenceRun, OccurrenceScope } from '../../api/client'
import { useBlockOccurrences, useOccurrenceRequests } from '../../api/hooks'

const SCOPE_NOTES: Record<string, string> = {
  auxiliary_request: 'Auxiliary request: showing the whole session.',
  no_session: 'This request has no session: showing only itself.',
  conversation_unavailable: 'Conversation not available: showing the whole session.',
}

function seqLabel(seq: number | null): string {
  return seq == null ? '—' : `#${seq}`
}

function runLabel(run: OccurrenceRun): string {
  return run.from_seq === run.to_seq ? seqLabel(run.from_seq) : `${seqLabel(run.from_seq)}–${seqLabel(run.to_seq)}`
}

function EntryRow({ entry, onOpen }: { entry: OccurrenceEntry; onOpen?: (entry: OccurrenceEntry) => void }) {
  const label = `${seqLabel(entry.session_seq)}${entry.conversation_code ? ` ${entry.conversation_code}` : ''}`
  const detail = `${entry.token_count.toLocaleString()} tokens${entry.context_fidelity !== 'complete' ? ` · ${entry.context_fidelity}` : ''}`
  if (entry.is_current) {
    return <li className="flex items-center justify-between gap-2 rounded px-2 py-1 text-xs font-medium bg-[var(--accent-soft)]"><span>{label} <span className="font-normal text-[var(--text-muted)]">(this request)</span></span><span className="tabular-nums text-[var(--text-muted)]">{detail}</span></li>
  }
  return (
    <li>
      <button type="button" disabled={!onOpen} onClick={() => onOpen?.(entry)} aria-label={`Open request ${label} with this block selected`}
        className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-xs hover:bg-[var(--surface-muted)] disabled:cursor-default disabled:hover:bg-transparent">
        <span>{label}</span><span className="tabular-nums text-[var(--text-muted)]">{detail}</span>
      </button>
    </li>
  )
}

function ExpandedRun({ requestId, blockId, scope, run, onOpen }: {
  requestId: string; blockId: number; scope: OccurrenceScope; run: OccurrenceRun; onOpen?: (entry: OccurrenceEntry) => void
}) {
  const query = useOccurrenceRequests(requestId, blockId, scope, { from: run.from_position, to: run.to_position })
  if (query.isLoading) return <p className="px-2 py-1 text-xs text-[var(--text-muted)]">Loading requests…</p>
  if (query.isError || !query.data) return <p className="px-2 py-1 text-xs text-[var(--danger)]">Could not load these requests.</p>
  return (
    <>
      <ul className="max-h-48 overflow-y-auto">{query.data.requests.map((entry) => <EntryRow key={entry.request_id} entry={entry} onOpen={onOpen} />)}</ul>
      {query.data.has_more && <p className="px-2 py-1 text-[10px] text-[var(--text-muted)]">Showing the first {query.data.requests.length} requests of this run.</p>}
    </>
  )
}

/** "Present in": the requests whose context contains this block, as runs, with token totals. */
export function BlockOccurrences({ requestId, blockId, onOpen }: {
  requestId: string
  blockId: number
  /** Opens an occurrence's request with its block selected; entries are not clickable without it. */
  onOpen?: (entry: OccurrenceEntry) => void
}) {
  const [scope, setScope] = useState<OccurrenceScope>('conversation')
  const [expanded, setExpanded] = useState<number | null>(null)
  const query = useBlockOccurrences(requestId, blockId, scope)
  // A malformed response is shown as an error rather than crashing the inspector.
  const data = query.data?.identity && query.data.totals ? query.data : undefined

  return (
    <section className="mt-4 space-y-2 border-t border-[var(--border)] pt-3" aria-label="Present in">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold">Present in</h4>
        <div role="group" aria-label="Occurrence scope" className="flex gap-1">
          {(['conversation', 'session'] as const).map((value) => (
            <button key={value} type="button" aria-pressed={scope === value} onClick={() => { setScope(value); setExpanded(null) }}
              className={`app-button min-h-7 px-2 py-0.5 text-[11px] ${scope === value ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>
              {value === 'conversation' ? 'Conversation' : 'Session'}
            </button>
          ))}
        </div>
      </div>

      {query.isLoading && <p className="text-xs text-[var(--text-muted)]">Loading…</p>}
      {(query.isError || (query.data && !data)) && !data && <p className="text-xs text-[var(--danger)]">Could not load where this block occurs.</p>}

      {data && (
        <div className={query.isFetching ? 'opacity-60' : undefined}>
          {data.scope_note && <p className="mb-1 text-[11px] text-[var(--text-muted)]">{SCOPE_NOTES[data.scope_note] ?? data.scope_note}</p>}
          {data.identity.kind === 'none' ? (
            <p className="text-xs text-[var(--text-muted)]">This block has no content identity (hidden or empty content), so it cannot be matched across requests.</p>
          ) : (
            <>
              <p className="text-xs">
                <strong className="tabular-nums">{data.totals.request_count.toLocaleString()}</strong> of {data.totals.scope_request_count.toLocaleString()} requests
                {data.totals.occurrence_count > data.totals.request_count && <> ({data.totals.occurrence_count.toLocaleString()} occurrences)</>}
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                {data.totals.tokens_per_occurrence != null && <>{data.totals.tokens_per_occurrence.toLocaleString()} tokens each · </>}
                <span title="Sum of the block's visible tokens over every occurrence; not the provider's billed total">{data.totals.total_visible_tokens.toLocaleString()} visible tokens total</span>
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                First {seqLabel(data.totals.first_seen_session_seq)} · last {seqLabel(data.totals.last_seen_session_seq)} · {data.totals.in_latest_request_of_scope ? 'still in the latest request' : 'not in the latest request'}
              </p>
              {Object.entries(data.totals.fidelity_counts).some(([key]) => key !== 'complete') && (
                <p className="text-[11px] text-[var(--text-muted)]" title="Partial and opaque requests hold only part of their context in the capture">
                  {Object.entries(data.totals.fidelity_counts).filter(([key]) => key !== 'complete').map(([key, count]) => `${count} ${key}`).join(' · ')} request{Object.entries(data.totals.fidelity_counts).filter(([key]) => key !== 'complete').reduce((sum, [, count]) => sum + count, 0) === 1 ? '' : 's'}
                </p>
              )}
              <ul className="mt-2 space-y-1">
                {data.ranges.map((run, index) => {
                  const open = expanded === index
                  return (
                    <li key={run.from_position}>
                      <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : index)}
                        className="flex w-full items-center justify-between gap-2 rounded border border-[var(--border)] px-2 py-1 text-left text-xs hover:bg-[var(--surface-muted)]">
                        <span className="font-medium">{runLabel(run)}</span>
                        <span className="tabular-nums text-[var(--text-muted)]">{run.request_count.toLocaleString()} request{run.request_count === 1 ? '' : 's'}</span>
                      </button>
                      {open && <ExpandedRun requestId={requestId} blockId={blockId} scope={scope} run={run} onOpen={onOpen} />}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}
