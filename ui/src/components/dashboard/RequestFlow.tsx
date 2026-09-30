import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { RequestCard } from './RequestCard'
import type { RequestCardItem } from './RequestCard'

/** Scrollable ordered row; screens supply items, selection, and the More action. */
export function RequestFlow({ items, bare = false, trailingAction, headerActions, compact = false, selectedId, onSelect }: {
  items: RequestCardItem[]
  bare?: boolean
  trailingAction?: ReactNode
  headerActions?: ReactNode
  compact?: boolean
  selectedId?: string | null
  onSelect?: (id: string) => void
}) {
  const navigate = useNavigate()
  return <div className={bare ? 'min-w-0' : 'panel min-w-0'}>
    {!bare && <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 id="request-flow-title" className="section-title">Request flow</h2>
      {headerActions}
    </div>}
    {items.length === 0 ? <p className="py-4 text-center text-sm text-[var(--text-muted)]">No requests captured in this session yet.</p> :
      <div className="flex min-w-0 gap-2 overflow-x-auto overscroll-x-contain pb-2" role="group" tabIndex={0}
        aria-label="Request sequence; scroll horizontally for older requests">
        <ol aria-labelledby={bare ? undefined : 'request-flow-title'}
          aria-description="Most recent requests in this session, ordered newest first"
          className="flex w-max shrink-0 gap-2">
          {items.map((item) => <li key={item.id} className={`${compact ? 'w-32' : 'w-36'} shrink-0`}>
            <RequestCard item={item} compact={compact} selected={selectedId === item.id} selectable={!!onSelect}
              onActivate={() => {
                if (onSelect && selectedId !== item.id) onSelect(item.id)
                else navigate(`/requests/${item.id}`)
              }} />
          </li>)}
        </ol>
        {trailingAction && <div className="w-32 shrink-0">{trailingAction}</div>}
      </div>}
  </div>
}
