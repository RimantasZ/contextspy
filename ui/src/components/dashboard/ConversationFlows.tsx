import { Link } from 'react-router-dom'
import type { DashboardConversation, DashboardLiveData } from '../../api/client'
import { RequestFlow } from './RequestFlow'
import { RequestDensityToggle, RequestViewControls } from './RequestViewControls'
import { useConversationCardDensity } from './requestViewState'

export function evidenceLabel(group: DashboardConversation): string {
  if (group.evidence === 'fork') return `Confirmed fork from ${group.fork_parent_request_id?.slice(0, 8) ?? 'request'}`
  if (group.evidence === 'parallel_chains') return 'Confirmed parallel independent chains'
  if (group.evidence === 'stream_affinity') return 'Supported separate stream · corroborated by context'
  if (group.evidence === 'independent_agent_stream') return 'Separate agent stream · sustained chain and distinct context'
  if (group.evidence === 'distinct_stream_hint') return 'Distinct stream hint · sustained chain and distinct context'
  if (group.evidence === 'sustained_chain') return 'Sustained chain · continuity to other streams not established'
  if (group.evidence === 'unresolved_stream') return 'Sustained stream · relationship to other conversations not established'
  if (group.evidence === 'auxiliary') return 'Unclassified or one-off requests; these may move into a conversation as more evidence arrives.'
  return 'Session activity; separate streams not confirmed'
}

export function gapLabel(reason: string | null): string | null {
  if (reason === 'fork_branch') return 'Fork branch · parent shown in shared history'
  if (reason === 'graph_branch_unconfirmed') return 'Graph branch · separate conversation not confirmed'
  if (reason === 'ambiguous') return 'Lineage gap · ambiguous parent'
  if (reason === 'unresolved_exact') return 'Lineage gap · provider parent missing'
  if (reason === 'unavailable') return 'Lineage gap · context unavailable'
  if (reason === 'root') return 'Lineage gap · no parent established'
  if (reason === 'external') return 'Continues from another session'
  if (reason === 'stream_resume_unlinked') return 'Same stream · direct predecessor not established'
  return null
}

export function ConversationFlows({ data, selectedId, onSelect, compact, onShowSequence }: {
  data: DashboardLiveData
  selectedId: string | null
  onSelect: (id: string) => void
  compact: boolean
  onShowSequence: () => void
}) {
  const groups = [...(data.conversations ?? []), ...(data.auxiliary ? [data.auxiliary] : [])]
  const [groupCompact, setGroupCompact] = useConversationCardDensity(data.active_session?.id ?? '', compact)
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
        <section key={group.key} className="min-w-0 py-4 first:pt-0 last:pb-0" aria-label={group.label}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
              <h3 className="section-title">{group.label}</h3>
              <p className="text-xs text-[var(--text-muted)]">{evidenceLabel(group)}</p>
              {group.unlinked_segment_count > 0 && (
                <p className="text-xs text-[var(--warning)]">
                  {group.unlinked_segment_count} unlinked lineage segment{group.unlinked_segment_count === 1 ? '' : 's'}
                </p>
              )}
            </div>
            <RequestDensityToggle compact={groupCompact(group.key)} onChange={(value) => setGroupCompact(group.key, value)}
              label={`${group.label} card detail`} />
          </div>
          <RequestFlow
            items={group.recent_segments.flatMap((segment) => segment.request_flow)}
            bare
            selectedId={selectedId}
            onSelect={onSelect}
            compact={groupCompact(group.key)}
            trailingAction={group.has_older_requests && data.active_session ? (
              <Link className="app-button h-full w-full text-center" to={`/sessions/${data.active_session.id}?view=lineage&conversation=${encodeURIComponent(group.key)}`}>
                More ({group.request_count} total) →
              </Link>
            ) : null}
          />
        </section>
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
