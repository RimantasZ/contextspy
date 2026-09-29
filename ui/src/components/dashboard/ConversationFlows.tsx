import { Link } from 'react-router-dom'
import type { DashboardLiveData } from '../../api/client'
import { ConversationGroupRow } from './ConversationGroupRow'
import { RequestFlow } from './RequestFlow'
import { RequestViewControls } from './RequestViewControls'
import { useConversationCardDensity } from './requestViewState'
export { evidenceLabel, gapLabel } from './conversationPresentation'

export function ConversationFlows({ data, selectedId, onSelect, compact, onShowSequence }: {
  data: DashboardLiveData
  selectedId: string | null
  onSelect: (id: string) => void
  compact: boolean
  onShowSequence: () => void
}) {
  const groups = [...(data.conversations ?? []), ...(data.auxiliary ? [data.auxiliary] : [])]
  const [groupCompact, setGroupCompact] = useConversationCardDensity(data.active_session?.id ?? '')
  if (groups.length === 0) {
    return <div className="space-y-2">
      {data.request_flow.length > 0 && <p className="text-xs text-[var(--warning)]">Grouping unavailable; showing ungrouped session requests. Parent comparisons are unavailable.</p>}
      <RequestFlow items={data.request_flow} selectedId={selectedId} onSelect={onSelect} compact={compact}
        headerActions={<RequestViewControls layout="conversations" onLayoutChange={onShowSequence} groupCount={0} />} />
    </div>
  }
  return (
    <div className="panel min-w-0" aria-label="Session request sequences">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="section-title">Conversations</h2>
        <RequestViewControls layout="conversations" onLayoutChange={onShowSequence}
          groupCount={data.conversation_count + (data.auxiliary ? 1 : 0)} />
      </div>
      <div className="divide-y divide-[var(--border)]">
      {groups.map((group) => (
        <ConversationGroupRow key={group.key} group={group}
            items={group.recent_segments.flatMap((segment) => segment.request_flow)}
            selectedId={selectedId} onSelect={onSelect}
            compact={groupCompact(group.key)} onCompactChange={(value) => setGroupCompact(group.key, value)}
            notice={group.unlinked_segment_count > 0 && <p className="text-xs text-[var(--warning)]">
              {group.unlinked_segment_count} unlinked lineage segment{group.unlinked_segment_count === 1 ? '' : 's'}
            </p>}
            trailingAction={group.has_older_requests && data.active_session ? (
              <Link className="app-button h-full w-full text-center" to={`/sessions/${data.active_session.id}?view=lineage&conversation=${encodeURIComponent(group.key)}`}>
                More ({group.request_count} total) →
              </Link>
            ) : null}
          />
      ))}
      </div>
      {data.has_more_conversations && data.active_session && (
        <Link className="mt-4 inline-block text-sm text-[var(--accent-soft-text)]" to={`/sessions/${data.active_session.id}?view=lineage&layout=conversations`}>
          View all {data.conversation_count} conversations
        </Link>
      )}
    </div>
  )
}
