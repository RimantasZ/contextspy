import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSessionSequence } from '../../api/hooks'
import { sessionsApi } from '../../api/client'
import type { SessionConversation, SessionConversationPage, SessionConversationsData } from '../../api/client'

type LoadedConversationPage = {
  segments: SessionConversationPage['segments']
  nextCursor: string | null
  continuesEarlier: boolean
}

/** Keeps paged display data tied to the capture revision, including deep links. */
export function useSessionConversationPages(data: SessionConversationsData, initialGroupKey?: string | null) {
  const queryClient = useQueryClient()
  const sequence = useSessionSequence(data.session_id)
  const currentRevision = useRef(data.revision)
  const currentSequenceRevision = useRef(sequence.data?.revision)
  const activeLoad = useRef(0)
  currentRevision.current = data.revision
  currentSequenceRevision.current = sequence.data?.revision
  const [extraSequence, setExtraSequence] = useState<NonNullable<typeof sequence.data>['request_flow']>([])
  const [nextSequenceCursor, setNextSequenceCursor] = useState<string | null>(null)
  const [sequenceRevision, setSequenceRevision] = useState<string | null>(null)
  const sequenceCursor = sequenceRevision === sequence.data?.revision ? nextSequenceCursor : sequence.data?.next_cursor ?? null
  const sequenceItems = [...(sequence.data?.request_flow ?? []), ...(sequenceRevision === sequence.data?.revision ? extraSequence : [])]
  const [extraGroups, setExtraGroups] = useState<SessionConversation[]>([])
  const [nextGroupOffset, setNextGroupOffset] = useState<number | null>(data.next_group_offset)
  const [loaded, setLoaded] = useState<Record<string, LoadedConversationPage>>({})
  const [jumped, setJumped] = useState<Record<string, SessionConversationPage | undefined>>({})
  const [indexLimit, setIndexLimit] = useState<Record<string, number>>({})
  const [loadingKey, setLoadingKey] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [loadedRevision, setLoadedRevision] = useState(data.revision)
  const [linkedLoadFailed, setLinkedLoadFailed] = useState(false)
  const [linkedAttempt, setLinkedAttempt] = useState(0)
  const linkedGroupPresent = !initialGroupKey || initialGroupKey === data.auxiliary?.key ||
    data.conversations.some((group) => group.key === initialGroupKey) ||
    (loadedRevision === data.revision && extraGroups.some((group) => group.key === initialGroupKey))

  useEffect(() => {
    if (sequence.data && sequenceRevision !== sequence.data.revision) {
      activeLoad.current += 1
      setLoadingKey(null)
      setExtraSequence([])
      setNextSequenceCursor(sequence.data.next_cursor)
      setSequenceRevision(sequence.data.revision)
    }
  }, [sequence.data, sequenceRevision])

  useEffect(() => {
    if (loadedRevision !== data.revision) {
      activeLoad.current += 1
      setLoadingKey(null)
      setExtraGroups([])
      setNextGroupOffset(data.next_group_offset)
      setLoaded({})
      setJumped({})
      setIndexLimit({})
      setLinkedLoadFailed(false)
      setLoadedRevision(data.revision)
      setNotice('Capture changed. Conversation pages were refreshed.')
    }
  }, [data.revision, data.next_group_offset, loadedRevision])

  const loadFailed = useCallback((error: unknown, message: string) => {
    if (error instanceof Error && error.message.includes('API error 409')) {
      queryClient.invalidateQueries({ queryKey: ['session-conversations', data.session_id] })
      setNotice('Capture changed. Refreshing conversation groups and pages…')
    } else setNotice(message)
  }, [queryClient, data.session_id])

  useEffect(() => {
    if (linkedGroupPresent || !initialGroupKey || linkedLoadFailed || loadedRevision !== data.revision) return
    let active = true
    sessionsApi.conversations(data.session_id, 0, data.revision, initialGroupKey).then((page) => {
      if (active) setExtraGroups((previous) => {
        const added = page.conversations.filter((group) => !previous.some((item) => item.key === group.key))
        return added.length ? [...previous, ...added] : previous
      })
    }).catch((error: unknown) => {
      if (active) {
        setLinkedLoadFailed(true)
        loadFailed(error, 'Linked conversation could not be loaded.')
      }
    })
    return () => { active = false }
  }, [linkedGroupPresent, initialGroupKey, data.session_id, data.revision, loadedRevision, linkedAttempt, linkedLoadFailed, loadFailed])

  function retryLinkedGroup() {
    setLinkedLoadFailed(false)
    setNotice('')
    setLinkedAttempt((previous) => previous + 1)
  }

  async function loadSequence() {
    if (!sequenceCursor || !sequence.data || loadingKey) return
    const revision = sequence.data.revision
    const loadId = ++activeLoad.current
    setLoadingKey('sequence')
    try {
      const page = await sessionsApi.sequence(data.session_id, revision, sequenceCursor)
      if (currentSequenceRevision.current !== revision) return
      setExtraSequence((previous) => [...previous, ...page.request_flow])
      setNextSequenceCursor(page.next_cursor)
    } catch (error) {
      if (currentSequenceRevision.current !== revision) return
      if (error instanceof Error && error.message.includes('API error 409')) {
        queryClient.invalidateQueries({ queryKey: ['session-sequence', data.session_id] })
        setNotice('Capture changed. Refreshing the request sequence…')
      } else setNotice('Older requests could not be loaded. Try again.')
    } finally { if (activeLoad.current === loadId) setLoadingKey(null) }
  }

  async function loadGroups() {
    if (nextGroupOffset == null || loadingKey) return
    const revision = data.revision
    const loadId = ++activeLoad.current
    setLoadingKey('groups')
    try {
      const page = await sessionsApi.conversations(data.session_id, nextGroupOffset, revision)
      if (currentRevision.current !== revision) return
      setExtraGroups((previous) => {
        const existing = new Set([...data.conversations, ...previous].map((group) => group.key))
        return [...previous, ...page.conversations.filter((group) => !existing.has(group.key))]
      })
      setNextGroupOffset(page.next_group_offset)
    } catch (error) {
      if (currentRevision.current === revision) loadFailed(error, 'Conversation groups could not be loaded. Try again.')
    } finally {
      if (activeLoad.current === loadId) setLoadingKey(null)
    }
  }

  async function loadEarlier(group: SessionConversation) {
    if (loadingKey) return
    const cursor = loaded[group.key]?.nextCursor ?? group.next_request_cursor
    if (!cursor) return
    const revision = data.revision
    const loadId = ++activeLoad.current
    setLoadingKey(group.key)
    try {
      const page = await sessionsApi.conversationRequests(data.session_id, group.key, revision, cursor)
      if (currentRevision.current !== revision) return
      setLoaded((previous) => ({
        ...previous,
        [group.key]: {
          segments: [...(previous[group.key]?.segments ?? []), ...page.segments],
          nextCursor: page.next_cursor,
          continuesEarlier: page.continues_earlier,
        },
      }))
    } catch (error) {
      if (currentRevision.current === revision) loadFailed(error, 'The request sequence could not be loaded. Try again.')
    } finally {
      if (activeLoad.current === loadId) setLoadingKey(null)
    }
  }

  async function jumpToSegment(group: SessionConversation, cursor: string | null) {
    if (loadingKey) return
    const revision = data.revision
    const loadId = ++activeLoad.current
    setLoadingKey(`jump:${group.key}`)
    try {
      const page = await sessionsApi.conversationRequests(data.session_id, group.key, revision, cursor)
      if (currentRevision.current !== revision) return
      setJumped((previous) => ({ ...previous, [group.key]: page }))
    } catch (error) {
      if (currentRevision.current === revision) loadFailed(error, 'The segment could not be loaded. Try again.')
    } finally {
      if (activeLoad.current === loadId) setLoadingKey(null)
    }
  }

  const groups = [
    ...(loadedRevision === data.revision ? [...data.conversations, ...extraGroups] : data.conversations),
    ...(data.auxiliary ? [data.auxiliary] : []),
  ]
  return { sequence, sequenceItems, sequenceCursor, groups, loaded, jumped, setJumped,
    indexLimit, setIndexLimit, nextGroupOffset, loadingKey, notice, setNotice,
    linkedLoadFailed, retryLinkedGroup, loadSequence, loadGroups, loadEarlier, jumpToSegment }
}
