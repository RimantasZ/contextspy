// Copyright 2026 Rimantas Zukaitis
import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useRequestContext } from '../api/hooks'
import type { SessionConversationsData } from '../api/client'
import { ConversationGroupRow } from './dashboard/ConversationGroupRow'
import { gapLabel } from './dashboard/conversationPresentation'
import { RequestFlow } from './dashboard/RequestFlow'
import { RequestOverview } from './dashboard/RequestOverview'
import { RequestViewControls } from './dashboard/RequestViewControls'
import { useCompactRequestCards, useConversationCardDensity, useSelectedRequest } from './dashboard/requestViewState'
import { useSessionConversationPages } from './dashboard/useSessionConversationPages'
import { formatDateTimeCompact } from '../lib/format'

export function SessionConversationSequences({ data, initialGroupKey, layout = 'conversations', onLayoutChange = () => {} }: {
  data: SessionConversationsData; initialGroupKey?: string | null
  layout?: 'sequence' | 'conversations'; onLayoutChange?: (layout: 'sequence' | 'conversations') => void
}) {
  const navigate = useNavigate()
  const { sequence, sequenceItems, sequenceCursor, groups, loaded, jumped, setJumped,
    indexLimit, setIndexLimit, nextGroupOffset, loadingKey, notice, setNotice,
    linkedLoadFailed, retryLinkedGroup, loadSequence, loadGroups, loadEarlier, jumpToSegment,
  } = useSessionConversationPages(data, initialGroupKey)
  const [compact, setCompact] = useCompactRequestCards(data.session_id)
  const [groupCompact, setGroupCompact] = useConversationCardDensity(data.session_id)
  const newestId = sequence.data?.request_flow[0]?.id ?? null
  const [selectedId, setSelectedId] = useSelectedRequest(newestId)
  const context = useRequestContext(data.session_id, selectedId, sequence.data?.revision)

  useEffect(() => {
    if (selectedId && newestId && selectedId !== newestId &&
        context.error instanceof Error && context.error.message.includes('API error 404')) {
      setSelectedId(newestId)
      setNotice('Selected request is no longer in this session; showing the newest request.')
    }
  }, [context.error, newestId, selectedId, setSelectedId, setNotice])

  if (data.conversation_count === 0 && !data.auxiliary && !sequence.data?.request_count) {
    return <div className="py-12 text-center text-sm text-[var(--text-muted)]">No invocations captured yet.</div>
  }

  return (
    <div className="min-w-0 space-y-4">
      <p className="text-xs text-[var(--text-muted)]">
        {data.conversation_count} supported conversation{data.conversation_count === 1 ? '' : 's'} · {data.auxiliary_request_count ?? 0} auxiliary request{(data.auxiliary_request_count ?? 0) === 1 ? '' : 's'} · {data.diagnostic_path_count ?? data.lineage_fragment_count} diagnostic path{(data.diagnostic_path_count ?? data.lineage_fragment_count) === 1 ? '' : 's'}
      </p>
      <div role="status" aria-live="polite" className="text-xs text-[var(--text-muted)]">{notice} {linkedLoadFailed && <button type="button" className="app-button ml-2 min-h-7 py-1 text-xs" onClick={retryLinkedGroup}>Retry linked conversation</button>}</div>
      <RequestOverview activity={sequence.data?.activity ?? []} onOpenRequest={(id) => navigate(`/requests/${id}`)}
          change={context.data?.context_change ?? (selectedId === newestId ? groups.find((group) => group.latest_request_id === selectedId)?.context_change ?? null : null)}
          pending={!!selectedId && context.isLoading} failed={!!selectedId && context.isError}
        />
      {layout === 'sequence' ? (
        sequence.isLoading ? <div className="panel text-sm text-[var(--text-muted)]">Loading request sequence…</div>
        : sequence.error || !sequence.data ? <p role="alert" className="notice-warning">Request sequence could not be loaded.</p>
        : <RequestFlow items={sequenceItems} compact={compact} selectedId={selectedId} onSelect={setSelectedId}
            headerActions={<RequestViewControls compact={compact} onCompactChange={setCompact} layout={layout} onLayoutChange={onLayoutChange}
              groupCount={data.conversation_count + (data.auxiliary ? 1 : 0)} />}
            trailingAction={sequenceCursor && <button type="button" className="app-button h-full w-full text-center" disabled={!!loadingKey} onClick={loadSequence}>
              {loadingKey === 'sequence' ? 'Loading…' : `More (${sequence.data.request_count} total) →`}
            </button>} />
      ) : <div className="panel min-w-0" aria-label="Session request sequences">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="section-title">Conversations</h2>
          <RequestViewControls layout="conversations" onLayoutChange={onLayoutChange}
            groupCount={data.conversation_count + (data.auxiliary ? 1 : 0)} />
        </div>
        <div className="divide-y divide-[var(--border)]">
        {groups.map((group) => {
          const more = loaded[group.key]
          const segments = [...group.recent_segments, ...(more?.segments ?? [])]
          const nextCursor = more ? more.nextCursor : group.next_request_cursor
          const focused = jumped[group.key]
          return (
            <ConversationGroupRow key={group.key} group={group}
              items={segments.flatMap((segment) => segment.request_flow)}
              compact={groupCompact(group.key)} onCompactChange={(value) => setGroupCompact(group.key, value)}
              selectedId={selectedId} onSelect={setSelectedId}
              meta={<p className="text-xs text-[var(--text-muted)]">
                {group.request_count} represented request{group.request_count === 1 ? '' : 's'} · latest {formatDateTimeCompact(group.latest_activity)} · {group.segment_count} lineage segment{group.segment_count === 1 ? '' : 's'}
                {' · '}<Link className="underline hover:text-[var(--text)]" aria-label={`Hot spots of ${group.label}`}
                  to={`/sessions/${data.session_id}?view=hotspots&conversation=${encodeURIComponent(group.key)}`}>Hot spots</Link>
              </p>}
              notice={group.unassigned_request_count > 0 && <p className="text-xs text-[var(--warning)]">{group.unassigned_request_count} request{group.unassigned_request_count === 1 ? '' : 's'} with uncertain stream membership</p>}
              beforeFlow={group.segment_index.length > 1 && (
                <details className="mb-3 rounded border border-[var(--border)] p-2 text-xs">
                  <summary className="cursor-pointer font-medium">Segment index · {group.segment_index.length} paths and breaks</summary>
                  <div className="mt-2 flex max-h-36 flex-wrap gap-2 overflow-y-auto">
                    {group.segment_index.slice(0, indexLimit[group.key] ?? 20).map((segment) => (
                      <button key={segment.key} type="button" className="app-button min-h-7 py-1 text-xs" onClick={() => jumpToSegment(group, segment.cursor_before)}>
                        #{segment.latest_session_seq ?? '—'} · {segment.request_count} requests · {gapLabel(segment.gap_reason) ?? 'Accepted continuation'}
                      </button>
                    ))}
                    {group.segment_index.length > (indexLimit[group.key] ?? 20) && <button type="button" className="app-button min-h-7 py-1 text-xs" onClick={() => setIndexLimit((previous) => ({ ...previous, [group.key]: (previous[group.key] ?? 20) + 20 }))}>More segments</button>}
                  </div>
                </details>
              )}
              trailingAction={nextCursor ? (
                  <button type="button" className="app-button h-full w-full text-center" disabled={!!loadingKey} onClick={() => loadEarlier(group)}>
                    {loadingKey === group.key ? 'Loading…' : `More (${group.request_count} total) →`}
                  </button>
                ) : null}
              afterFlow={<>
              {more?.continuesEarlier && <p className="mt-2 text-xs text-[var(--text-muted)]">This linked segment continues on the next page.</p>}
              {focused && <div className="mt-4 rounded border border-[var(--border)] p-3">
                <div className="mb-2 flex justify-between gap-2"><h4 className="text-sm font-medium">Selected segment window</h4><button className="app-button min-h-7 py-1 text-xs" onClick={() => setJumped((previous) => ({ ...previous, [group.key]: undefined }))}>Close</button></div>
                <RequestFlow items={focused.segments.flatMap((segment) => segment.request_flow)} bare compact={groupCompact(group.key)} selectedId={selectedId} onSelect={setSelectedId} />
              </div>}
              </>}
            />
          )
        })}
        </div>
      {nextGroupOffset != null && <button type="button" className="app-button mt-4" disabled={!!loadingKey} onClick={loadGroups}>{loadingKey === 'groups' ? 'Loading…' : 'Show more conversations'}</button>}
      </div>}
    </div>
  )
}
