import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { sessionsApi } from '../api/client'
import type { SessionConversation, SessionConversationsData } from '../api/client'
import { SessionConversationSequences } from './SessionConversationSequences'

function group(key: string, seq: number, gap: SessionConversation['recent_segments'][number]['gap_reason'] = 'root'): SessionConversation {
  const id = `r${seq}`
  return {
    key, label: key, evidence: 'default', fork_parent_request_id: null,
    latest_request_id: id, latest_session_seq: seq, latest_activity: `2026-09-18T08:${String(seq).padStart(2, '0')}:00`,
    latest_parent_state: 'root', request_count: 2, unassigned_request_count: 1,
    unlinked_segment_count: 1, segment_count: 2, has_older_requests: true, next_request_cursor: 'cursor-1',
    segment_index: [{ key: id, gap_reason: gap, first_request_id: id, first_session_seq: seq, latest_session_seq: seq, newest_request_id: id, request_count: 1, cursor_before: null }],
    recent_segments: [{ key: id, gap_reason: gap, first_request_id: id, first_session_seq: seq, latest_session_seq: seq, newest_request_id: id, request_count: 1, request_flow: [{
      id, session_seq: seq, timestamp: `2026-09-18T08:${String(seq).padStart(2, '0')}:00`, model: 'gpt-test', duration_ms: 10, status_code: 200, invocation_outcome: 'completed',
      tokens_total_input: 100, tokens_total_output: 5, parent_request_id: null, parent_state: 'root', certainty: null, confidence: null,
      membership_state: 'unassigned', shared_history: false, fork_status: 'none',
    }] }],
    context_change: { request_id: id, session_seq: seq, tokens_total_input: 100, parent_request_id: null, parent_session_seq: null,
      parent_state: 'root', parent_confidence: null, external_parent: false, first_conversation: false,
      token_delta: null, comparison_fidelity: 'unavailable', block_changes: [] },
  }
}

const base: SessionConversationsData = {
  session_id: 's1', revision: 'v1', conversation_count: 1, confirmed_parallel_streams: 0,
  lineage_fragment_count: 28, primary_key: 'primary', conversations: [group('primary', 9, 'graph_branch_unconfirmed')], next_group_offset: null,
}

describe('session conversation sequences', () => {
  function show(data: SessionConversationsData) {
    return render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><SessionConversationSequences data={data} /></MemoryRouter></QueryClientProvider>)
  }
  it('keeps diagnostic paths out of the group count and marks uncertain branches', () => {
    show(base)
    expect(screen.getByText('1 conversation sequence · 28 diagnostic paths')).toBeTruthy()
    expect(screen.getByText('Graph branch · separate conversation not confirmed')).toBeTruthy()
    expect(screen.getByRole('link', { name: /stream membership uncertain/ }).getAttribute('href')).toBe('/requests/r9')
    expect(screen.queryByText('Conversation 2')).toBeNull()
  })

  it('loads earlier cards and additional confirmed groups without changing semantics', async () => {
    const earlier = vi.spyOn(sessionsApi, 'conversationRequests').mockResolvedValue({
      session_id: 's1', revision: 'v1', group_key: 'primary', next_cursor: null, continues_earlier: false,
      segments: [group('older', 8).recent_segments[0]],
    })
    const extra = vi.spyOn(sessionsApi, 'conversations').mockResolvedValue({
      ...base, conversations: [group('second', 10)], next_group_offset: null,
    })
    show({ ...base, conversation_count: 2, next_group_offset: 1 })
    await userEvent.click(screen.getByRole('button', { name: 'Show earlier requests' }))
    expect(await screen.findByRole('link', { name: /Request #8/ })).toBeTruthy()
    expect(earlier).toHaveBeenCalledWith('s1', 'primary', 'v1', 'cursor-1')
    await userEvent.click(screen.getByRole('button', { name: 'Show more conversations' }))
    const second = await screen.findByRole('region', { name: 'second' })
    expect(within(second).getByRole('link', { name: /Request #10/ })).toBeTruthy()
    expect(extra).toHaveBeenCalledWith('s1', 1, 'v1')
    earlier.mockRestore()
    extra.mockRestore()
  })

  it('revalidates a selected group that moves beyond the preview after a revision', async () => {
    const secondary = group('second', 10)
    const pinned = vi.spyOn(sessionsApi, 'conversations').mockResolvedValue({
      ...base, revision: 'v2', conversation_count: 2, conversations: [secondary], next_group_offset: null,
    })
    const { rerender } = show({ ...base, conversation_count: 2, conversations: [base.conversations[0], secondary] })
    await userEvent.click(within(screen.getByRole('region', { name: 'second' })).getByRole('button', { name: 'Show context' }))
    rerender(<QueryClientProvider client={new QueryClient()}><MemoryRouter><SessionConversationSequences data={{ ...base, revision: 'v2', conversation_count: 2, next_group_offset: 1 }} /></MemoryRouter></QueryClientProvider>)
    expect(await within(await screen.findByRole('region', { name: 'second' })).findByRole('button', { name: 'Showing context' })).toBeTruthy()
    expect(pinned).toHaveBeenCalledWith('s1', 0, 'v2', 'second')
    pinned.mockRestore()
  })
})
