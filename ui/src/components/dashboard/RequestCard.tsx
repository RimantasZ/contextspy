import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { DashboardRequestFlowItem, LineageRequestFlowItem } from '../../api/client'
import { formatDateTimeFull, formatDurationSeconds, formatTimeShort } from '../../lib/format'
import { conversationRequestLabel, formatCompactTokens } from './dashboardFormat'

/** Presentation-only card. RequestFlow supplies selection and navigation behavior. */
export type RequestCardItem = DashboardRequestFlowItem & Partial<LineageRequestFlowItem>

type Marker = { icon: string; label: string; description: string; uncertain: boolean }

const markers: Record<NonNullable<RequestCardItem['lineage_relation']>, Marker> = {
  exact: { icon: '↳', label: 'Exact predecessor', description: 'The provider explicitly linked this request to its predecessor.', uncertain: false },
  inferred: { icon: '≈', label: 'Inferred predecessor', description: 'ContextSpy inferred a direct predecessor from the captured context.', uncertain: false },
  suggested: { icon: '≈', label: 'Suggested predecessor', description: 'A possible predecessor was found, but the link is not confirmed.', uncertain: true },
  context_affinity: { icon: '⋯', label: 'Same stream; direct predecessor not established', description: 'Shared context places this request in the same conversation, but its direct predecessor is unknown.', uncertain: true },
  compaction_affinity: { icon: '⋯', label: 'Same stream after context reset; direct predecessor not established', description: 'Earlier retained context and a matching stream hint support this conversation after a context reset. The direct predecessor is still unknown.', uncertain: true },
  ambiguous: { icon: '?', label: 'Direct predecessor ambiguous', description: 'More than one request could be the direct predecessor.', uncertain: true },
  unresolved_exact: { icon: '!', label: 'Provider predecessor missing', description: 'The provider named a predecessor that ContextSpy could not link among captured requests.', uncertain: true },
  unavailable: { icon: '?', label: 'Predecessor context unavailable', description: 'There is not enough captured context to identify a predecessor.', uncertain: true },
  root: { icon: '○', label: 'No predecessor established', description: 'No direct predecessor was established for this request.', uncertain: true },
  external: { icon: '↗', label: 'Predecessor in another session', description: 'This request follows a predecessor captured in another session.', uncertain: false },
}

function statusOf(item: DashboardRequestFlowItem): { text: string; className: string } | null {
  if (item.invocation_outcome === 'failed' || (item.status_code != null && item.status_code >= 400)) {
    return { text: item.status_code != null && item.status_code >= 400 ? `Failed (${item.status_code})` : 'Failed', className: 'status-danger' }
  }
  if (item.invocation_outcome === 'incomplete') return { text: 'Incomplete', className: 'status-warning' }
  return null
}

function LineageIcon({ marker, focused }: { marker: Marker; focused: boolean }) {
  const iconRef = useRef<HTMLSpanElement>(null)
  const [tooltip, setTooltip] = useState<{ left: number; top: number; above: boolean } | null>(null)

  function showTooltip() {
    const rect = iconRef.current?.getBoundingClientRect()
    if (!rect) return
    const above = window.innerHeight - rect.bottom < 120 && rect.top > 120
    setTooltip({
      left: Math.max(8, Math.min(rect.right - 240, window.innerWidth - 248)),
      top: above ? rect.top - 8 : rect.bottom + 8,
      above,
    })
  }

  useEffect(() => {
    if (!focused) setTooltip(null)
    else showTooltip()
  }, [focused])

  useEffect(() => {
    if (!tooltip) return
    const hideTooltip = () => setTooltip(null)
    window.addEventListener('scroll', hideTooltip, true)
    window.addEventListener('resize', hideTooltip)
    return () => {
      window.removeEventListener('scroll', hideTooltip, true)
      window.removeEventListener('resize', hideTooltip)
    }
  }, [tooltip])

  return <>
    <span ref={iconRef} aria-hidden="true"
      onMouseEnter={showTooltip}
      onMouseLeave={() => { if (!focused) setTooltip(null) }}
      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border text-xs font-semibold ${marker.uncertain ? 'border-[var(--warning)] text-[var(--warning)]' : 'border-[var(--border)] text-[var(--accent-soft-text)]'}`}
    >{marker.icon}</span>
    {tooltip && createPortal(
      <div role="tooltip"
        className="pointer-events-none fixed z-[100] w-60 max-w-[calc(100vw-1rem)] rounded-md border border-[var(--border)] bg-[var(--chart-tooltip)] p-2.5 text-xs text-[var(--chart-tooltip-text)] shadow-lg"
        style={{ left: tooltip.left, top: tooltip.top, transform: tooltip.above ? 'translateY(-100%)' : undefined }}>
        <span className="font-semibold">{marker.label}</span>
        <span className="mt-1 block leading-relaxed">{marker.description}</span>
      </div>, document.body,
    )}
  </>
}

export function RequestCard({ item, compact = false, selected = false, selectable = false, onActivate }: {
  item: RequestCardItem
  compact?: boolean
  selected?: boolean
  selectable?: boolean
  onActivate: () => void
}) {
  const [focused, setFocused] = useState(false)
  const label = conversationRequestLabel(item.session_seq, item.id, item.conversation_code)
  const time = formatTimeShort(item.timestamp)
  const fullTime = formatDateTimeFull(item.timestamp)
  const status = statusOf(item)
  const marker = item.lineage_relation ? markers[item.lineage_relation] : null
  const meta = [item.model, item.duration_ms != null ? formatDurationSeconds(item.duration_ms) : null]
    .filter(Boolean).join(' · ')

  return <button type="button" aria-pressed={selectable ? selected : undefined} onClick={onActivate}
    onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
    aria-label={`Request ${label}, ${time}, input ${item.tokens_total_input.toLocaleString()} tokens, output ${item.tokens_total_output.toLocaleString()} tokens${marker ? `, ${marker.label}: ${marker.description}` : ''}${status ? `, ${status.text}` : ''}${item.shared_history ? ', shared history' : ''}${item.membership_state === 'provisional_unassigned' ? ', provisional stream membership' : item.membership_state === 'unassigned' ? ', stream membership uncertain' : ''}`}
    className={`block h-full w-full rounded-md border border-[var(--border)] text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus)] ${compact ? 'p-1.5' : 'p-2.5'} ${selectable && selected ? 'bg-[var(--surface-selected)] hover:bg-[var(--surface-selected)]' : 'bg-[var(--surface-muted)] hover:bg-[var(--surface-hover)]'}`}>
    <div className="flex items-center justify-between gap-2">
      <span className={`${compact ? 'text-xs' : 'text-sm'} font-semibold text-[var(--text)]`}>{label}</span>
      <span className="flex items-center gap-1">
        {compact && status && <span aria-hidden="true"
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.className === 'status-danger' ? 'bg-[var(--danger)]' : 'bg-[var(--warning)]'}`} />}
        {marker && <LineageIcon marker={marker} focused={focused} />}
      </span>
    </div>
    {compact ? <>
      <p className="mt-1 flex justify-between gap-1 text-[11px] tabular-nums text-[var(--text-muted)]">
        <span>{time}</span><span>{formatDurationSeconds(item.duration_ms)}</span>
      </p>
      <p className="mt-1 flex justify-between gap-1 text-[11px] tabular-nums text-[var(--text)]" aria-hidden="true">
        <span>↑ {formatCompactTokens(item.tokens_total_input)}</span>
        <span>↓ {formatCompactTokens(item.tokens_total_output)}</span>
      </p>
    </> : <>
      <span className="mt-0.5 block text-xs tabular-nums text-[var(--text-muted)]" title={fullTime}>{time}</span>
      {(meta || status) && <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
        {meta && <span className="truncate" title={meta}>{meta}</span>}
        {status && <span className={`app-badge ${status.className}`}>{status.text}</span>}
      </p>}
      <p className="mt-2 text-xs tabular-nums text-[var(--text)]"><span aria-hidden="true">↑ </span>{item.tokens_total_input.toLocaleString()} in</p>
      <p className="text-xs tabular-nums text-[var(--text)]"><span aria-hidden="true">↓ </span>{item.tokens_total_output.toLocaleString()} out</p>
      {item.parent_state && <p className="mt-2 text-[11px] text-[var(--text-muted)]">
        {item.lineage_relation === 'context_affinity' || item.lineage_relation === 'compaction_affinity'
          ? 'Same stream · direct predecessor not established'
          : item.parent_request_id
          ? `${item.certainty === 'inferred' ? `Inferred score ${Math.round((item.confidence ?? 0) * 100)}/100` : 'Exact'} parent · ${item.parent_request_id.slice(0, 8)}`
          : item.parent_state === 'root' ? 'No parent established' : `Parent ${item.parent_state.replace('_', ' ')}`}
        {item.shared_history ? ' · Shared history' : ''}
        {item.membership_state === 'provisional_unassigned' ? ' · Provisional stream' : item.membership_state === 'unassigned' ? ' · Stream uncertain' : ''}
      </p>}
    </>}
  </button>
}
