import { Link } from 'react-router-dom'
import type { DashboardConversation, DashboardLiveData } from '../../api/client'
import { RequestFlow } from './RequestFlow'

export function evidenceLabel(group: DashboardConversation): string {
  if (group.evidence === 'fork') return `Confirmed fork from ${group.fork_parent_request_id?.slice(0, 8) ?? 'request'}`
  if (group.evidence === 'parallel_chains') return 'Confirmed parallel independent chains'
  if (group.evidence === 'stream_affinity') return 'Supported separate stream · corroborated by context'
  if (group.evidence === 'independent_agent_stream') return 'Separate agent stream · sustained chain and distinct context'
  if (group.evidence === 'distinct_stream_hint') return 'Distinct stream hint · sustained chain and distinct context'
  if (group.evidence === 'sustained_chain') return 'Sustained chain · continuity to other streams not established'
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

export function ConversationFlows({ data, selectedKey, onSelect }: {
  data: DashboardLiveData
  selectedKey: string | null
  onSelect: (key: string) => void
}) {
  const groups = [...(data.conversations ?? []), ...(data.auxiliary ? [data.auxiliary] : [])]
  if (groups.length === 0) {
    return <div className="space-y-2">
      {data.request_flow.length > 0 && <p className="text-xs text-[var(--warning)]">Grouping unavailable; showing ungrouped session requests. Parent comparisons are unavailable.</p>}
      <RequestFlow items={data.request_flow} />
    </div>
  }
  return (
    <div className="space-y-4" aria-label="Session request sequences">
      {groups.map((group) => (
        <section key={group.key} className="panel min-w-0" aria-label={group.label}>
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="section-title">{group.label}</h2>
              <p className="mt-1 text-xs text-[var(--text-muted)]">{evidenceLabel(group)}</p>
              {group.unlinked_segment_count > 0 && (
                <p className="mt-1 text-xs text-[var(--warning)]">
                  {group.unlinked_segment_count} unlinked lineage segment{group.unlinked_segment_count === 1 ? '' : 's'}
                </p>
              )}
            </div>
            <button
              type="button"
              aria-pressed={selectedKey === group.key}
              onClick={() => onSelect(group.key)}
              className="app-button"
            >
              {selectedKey === group.key ? 'Showing context' : 'Show context'}
            </button>
          </div>
          <RequestFlow
            items={group.recent_segments.flatMap((segment) => segment.request_flow)}
            bare
            trailingAction={group.has_older_requests && data.active_session ? (
              <Link className="app-button h-full w-full text-center" to={`/sessions/${data.active_session.id}?view=lineage&conversation=${encodeURIComponent(group.key)}`}>
                More ({group.request_count} total) →
              </Link>
            ) : null}
          />
          {group.has_older_requests && <p className="mt-1 text-xs text-[var(--text-muted)]">Scroll right for older requests.</p>}
        </section>
      ))}
      {data.has_more_conversations && data.active_session && (
        <Link className="text-sm text-[var(--accent-soft-text)]" to={`/sessions/${data.active_session.id}?view=lineage`}>
          View all {data.conversation_count} conversations
        </Link>
      )}
    </div>
  )
}
