import type { ReactNode } from 'react'
import type { DashboardActivityPoint, DashboardContextChange } from '../../api/client'
import { ContextChangePanel } from './ContextChangePanel'
import { RequestActivityChart } from './RequestActivityChart'

/** Presentation-only overview; screens own selection, fetching, and request actions. */
export function RequestOverview({ activity, change, pending, failed, onOpenRequest, actions }: {
  activity: DashboardActivityPoint[]
  change: DashboardContextChange | null
  pending?: boolean
  failed?: boolean
  onOpenRequest?: (id: string) => void
  actions?: ReactNode
}) {
  return <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,.6fr)]">
    <RequestActivityChart activity={activity} />
    <ContextChangePanel change={change} pending={pending} failed={failed}
      onOpenRequest={onOpenRequest} actions={actions} />
  </div>
}
