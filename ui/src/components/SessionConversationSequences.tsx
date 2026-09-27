// Copyright 2026 Rimantas Zukaitis
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { sessionsApi } from '../api/client'
import type { SessionConversation, SessionConversationPage, SessionConversationsData } from '../api/client'
import { ContextChangePanel } from './dashboard/ContextChangePanel'
import { evidenceLabel, gapLabel } from './dashboard/ConversationFlows'
import { RequestFlow } from './dashboard/RequestFlow'
import { parseServerTimestamp } from './dashboard/dashboardFormat'

type Loaded = { segments: SessionConversationPage['segments']; nextCursor: string | null; continuesEarlier: boolean }

export function SessionConversationSequences({ data, initialGroupKey }: { data: SessionConversationsData; initialGroupKey?: string | null }) {
  const queryClient = useQueryClient()
  const [extraGroups, setExtraGroups] = useState<SessionConversation[]>([])
  const [nextGroupOffset, setNextGroupOffset] = useState<number | null>(data.next_group_offset)
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({})
  const [jumped, setJumped] = useState<Record<string, SessionConversationPage | undefined>>({})
  const [indexLimit, setIndexLimit] = useState<Record<string, number>>({})
  const [selectedKey, setSelectedKey] = useState<string | null>(initialGroupKey ?? null)
  const [loadingKey, setLoadingKey] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [loadedRevision, setLoadedRevision] = useState(data.revision)
  const currentRevision = useRef(data.revision)
  currentRevision.current = data.revision

  useEffect(() => {
    if (initialGroupKey) setSelectedKey(initialGroupKey)
  }, [initialGroupKey])

  useEffect(() => {
    if (loadedRevision !== data.revision) {
      setExtraGroups([])
      setNextGroupOffset(data.next_group_offset)
      setLoaded({})
      setJumped({})
      setIndexLimit({})
      setLoadedRevision(data.revision)
      setNotice('Capture changed. Conversation pages were refreshed.')
      if (selectedKey && selectedKey !== data.auxiliary?.key &&
          !data.conversations.some((group) => group.key === selectedKey)) {
        const revision = data.revision
        sessionsApi.conversations(data.session_id, 0, revision, selectedKey).then((page) => {
          if (currentRevision.current === revision) setExtraGroups(page.conversations)
        }).catch((error: unknown) => {
          if (currentRevision.current !== revision) return
          if (error instanceof Error && error.message.includes('API error 404')) {
            setSelectedKey(null)
            setNotice('Selected conversation changed. Showing the latest conversation.')
          } else {
            loadFailed(error, 'Selected conversation could not be revalidated. Try refreshing this view.')
          }
        })
      }
    }
  }, [data.revision, data.next_group_offset, loadedRevision, selectedKey])

  const groups = [
    ...(loadedRevision === data.revision ? [...data.conversations, ...extraGroups] : data.conversations),
    ...(data.auxiliary ? [data.auxiliary] : []),
  ]
  const selected = groups.find((group) => group.key === selectedKey) ?? groups[0] ?? null

  function loadFailed(error: unknown, message: string) {
    if (error instanceof Error && error.message.includes('API error 409')) {
      queryClient.invalidateQueries({ queryKey: ['session-conversations', data.session_id] })
      setNotice('Capture changed. Refreshing conversation groups and pages…')
    } else {
      setNotice(message)
    }
  }

  async function loadGroups() {
    if (nextGroupOffset == null || loadingKey) return
    setLoadingKey('groups')
    try {
      const page = await sessionsApi.conversations(data.session_id, nextGroupOffset, data.revision)
      setExtraGroups((previous) => {
        const existing = new Set([...data.conversations, ...previous].map((group) => group.key))
        return [...previous, ...page.conversations.filter((group) => !existing.has(group.key))]
      })
      setNextGroupOffset(page.next_group_offset)
    } catch (error) {
      loadFailed(error, 'Conversation groups could not be loaded. Try again.')
    } finally {
      setLoadingKey(null)
    }
  }

  async function loadEarlier(group: SessionConversation) {
    if (loadingKey) return
    const cursor = loaded[group.key]?.nextCursor ?? group.next_request_cursor
    if (!cursor) return
    setLoadingKey(group.key)
    try {
      const page = await sessionsApi.conversationRequests(data.session_id, group.key, data.revision, cursor)
      setLoaded((previous) => ({
        ...previous,
        [group.key]: {
          segments: [...(previous[group.key]?.segments ?? []), ...page.segments],
          nextCursor: page.next_cursor,
          continuesEarlier: page.continues_earlier,
        },
      }))
    } catch (error) {
      loadFailed(error, 'The request sequence could not be loaded. Try again.')
    } finally {
      setLoadingKey(null)
    }
  }

  async function jumpToSegment(group: SessionConversation, cursor: string | null) {
    if (loadingKey) return
    setLoadingKey(`jump:${group.key}`)
    try {
      const page = await sessionsApi.conversationRequests(data.session_id, group.key, data.revision, cursor)
      setJumped((previous) => ({ ...previous, [group.key]: page }))
    } catch (error) {
      loadFailed(error, 'The segment could not be loaded. Try again.')
    } finally {
      setLoadingKey(null)
    }
  }

  if (data.conversation_count === 0 && !data.auxiliary) {
    return <div className="py-12 text-center text-sm text-[var(--text-muted)]">No invocations captured yet.</div>
  }

  return (
    <div className="min-w-0 space-y-5">
      <div>
        <h2 className="section-title">Conversation sequences</h2>
        <p className="mt-1 text-sm text-[var(--text)]">
          {data.conversation_count} confirmed conversation{data.conversation_count === 1 ? '' : 's'} · {data.auxiliary_request_count ?? 0} auxiliary request{(data.auxiliary_request_count ?? 0) === 1 ? '' : 's'} · {data.lineage_fragment_count} diagnostic path{data.lineage_fragment_count === 1 ? '' : 's'}
        </p>
        <p className="mt-1 text-xs text-[var(--text-muted)]">Diagnostic paths are lineage evidence, not additional conversations. Session totals count each stored request once; shared history may appear in more than one sequence.</p>
      </div>
      <p role="status" aria-live="polite" className="text-xs text-[var(--text-muted)]">{notice}</p>
      {selected && <ContextChangePanel change={selected.context_change} />}
      <div className="space-y-4" aria-label="Session request sequences">
        {groups.map((group) => {
          const more = loaded[group.key]
          const segments = [...group.recent_segments, ...(more?.segments ?? [])]
          const nextCursor = more ? more.nextCursor : group.next_request_cursor
          const focused = jumped[group.key]
          return (
            <section key={group.key} className="panel min-w-0" aria-label={group.label}>
              <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="section-title">{group.label}</h3>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">{evidenceLabel(group)}</p>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    {group.request_count} represented request{group.request_count === 1 ? '' : 's'} · latest {new Date(parseServerTimestamp(group.latest_activity)).toLocaleString()} · {group.segment_count} lineage segment{group.segment_count === 1 ? '' : 's'}
                  </p>
                  {group.unassigned_request_count > 0 && <p className="mt-1 text-xs text-[var(--warning)]">{group.unassigned_request_count} request{group.unassigned_request_count === 1 ? '' : 's'} with uncertain stream membership</p>}
                </div>
                <button type="button" aria-pressed={selected?.key === group.key} className="app-button" onClick={() => setSelectedKey(group.key)}>
                  {selected?.key === group.key ? 'Showing context' : 'Show context'}
                </button>
              </div>
              {group.segment_index.length > 1 && (
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
              <RequestFlow
                items={segments.flatMap((segment) => segment.request_flow)}
                bare
                trailingAction={nextCursor ? (
                  <button type="button" className="app-button h-full w-full text-center" disabled={!!loadingKey} onClick={() => loadEarlier(group)}>
                    {loadingKey === group.key ? 'Loading…' : `More (${group.request_count} total) →`}
                  </button>
                ) : null}
              />
              {nextCursor && <p className="mt-1 text-xs text-[var(--text-muted)]">Scroll right for older requests.</p>}
              {more?.continuesEarlier && <p className="mt-2 text-xs text-[var(--text-muted)]">This linked segment continues on the next page.</p>}
              {focused && <div className="mt-4 rounded border border-[var(--border)] p-3">
                <div className="mb-2 flex justify-between gap-2"><h4 className="text-sm font-medium">Selected segment window</h4><button className="app-button min-h-7 py-1 text-xs" onClick={() => setJumped((previous) => ({ ...previous, [group.key]: undefined }))}>Close</button></div>
                <RequestFlow items={focused.segments.flatMap((segment) => segment.request_flow)} bare />
              </div>}
            </section>
          )
        })}
      </div>
      {nextGroupOffset != null && <button type="button" className="app-button" disabled={!!loadingKey} onClick={loadGroups}>{loadingKey === 'groups' ? 'Loading…' : 'Show more conversations'}</button>}
    </div>
  )
}
