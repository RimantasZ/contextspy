import type { ReactNode } from 'react'
import type { DashboardConversation } from '../../api/client'
import { RequestFlow } from './RequestFlow'
import type { RequestCardItem } from './RequestCard'
import { RequestDensityToggle } from './RequestViewControls'
import { evidenceLabel } from './conversationPresentation'

/** Reusable display of one backend-supported group; callers own data and paging. */
export type ConversationGroupIdentity = Pick<DashboardConversation,
  'key' | 'label' | 'evidence' | 'fork_parent_request_id'>

export function ConversationGroupRow({ group, items, compact, onCompactChange, selectedId, onSelect,
  meta, notice, beforeFlow, afterFlow, trailingAction,
}: {
  group: ConversationGroupIdentity
  items: RequestCardItem[]
  compact: boolean
  onCompactChange: (value: boolean) => void
  selectedId: string | null
  onSelect: (id: string) => void
  meta?: ReactNode
  notice?: ReactNode
  beforeFlow?: ReactNode
  afterFlow?: ReactNode
  trailingAction?: ReactNode
}) {
  return <section className="min-w-0 py-4 first:pt-0 last:pb-0" aria-label={group.label}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="section-title">{group.label}</h3>
        <p className="text-xs text-[var(--text-muted)]">{evidenceLabel(group)}</p>
        {meta}
        {notice}
      </div>
      <RequestDensityToggle compact={compact} onChange={onCompactChange}
        label={`${group.label} card detail`} />
    </div>
    {beforeFlow}
    <RequestFlow items={items} bare compact={compact} selectedId={selectedId} onSelect={onSelect}
      trailingAction={trailingAction} />
    {afterFlow}
  </section>
}
