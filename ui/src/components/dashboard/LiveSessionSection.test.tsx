import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { DashboardLiveData, LineageRequestFlowItem } from '../../api/client'
import { LiveSessionSection } from './LiveSessionSection'

let live: DashboardLiveData
vi.mock('../../api/hooks', () => ({
  useDashboardLive: () => ({ data: live, isLoading: false, isError: false }),
  useRequestContext: (_sessionId: string, requestId: string | null) => ({
    data: requestId ? { context_change: context(requestId) } : undefined,
    isLoading: false, isError: false,
  }),
  useEndSession: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))

function context(id: string) {
  return { request_id: id, session_seq: Number(id.slice(1)), tokens_total_input: 100,
    parent_request_id: null, parent_session_seq: null, parent_state: 'root' as const,
    parent_confidence: null, external_parent: false, first_conversation: false,
    token_delta: null, comparison_fidelity: 'unavailable' as const, block_changes: [] }
}

function card(seq: number): LineageRequestFlowItem {
  return { id: `r${seq}`, session_seq: seq, timestamp: '2026-09-18T08:00:00', model: 'test',
    duration_ms: 10, status_code: 200, invocation_outcome: 'completed',
    tokens_total_input: 100, tokens_total_output: 5, parent_request_id: null,
    parent_state: 'root', certainty: null, confidence: null, lineage_relation: 'root',
    membership_state: 'unassigned', shared_history: false, fork_status: 'none' }
}

function data(seqs: number[]): DashboardLiveData {
  return { active_session: { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00',
    request_count: seqs.length, tokens_total_input: 100 * seqs.length, tokens_total_output: 5 * seqs.length },
    request_flow: seqs.map(card), activity: [], context_change: context(`r${seqs[0]}`),
    conversations: seqs.slice(0, 2).map((seq, index) => ({
      key: `g${index}`, label: `Conversation ${index + 1}`, evidence: 'default',
      fork_parent_request_id: null, latest_request_id: `r${seq}`, latest_session_seq: seq,
      latest_parent_state: 'root', request_count: 1, unlinked_segment_count: 0,
      recent_segments: [{ key: `r${seq}`, gap_reason: 'root', request_flow: [card(seq)] }],
      has_older_requests: false, context_change: context(`r${seq}`),
    })), auxiliary: null, auxiliary_request_count: 0, conversation_count: 2,
    confirmed_parallel_streams: 1, lineage_fragment_count: 2, has_more_conversations: false,
    most_recent_conversation_key: 'g0' }
}

describe('live request layouts', () => {
  it('defaults to compact chronological sequence, selects requests, and follows a new head', () => {
    localStorage.removeItem('contextspy.compact-request-cards')
    live = data([9, 8])
    const { rerender } = render(<MemoryRouter><LiveSessionSection /></MemoryRouter>)
    const row = screen.getByRole('list', { name: 'Request flow' })
    expect(within(row).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('checkbox', { name: 'Compact mode' }).getAttribute('checked')).not.toBeNull()
    const older = within(row).getByRole('button', { name: /Request #8/ })
    fireEvent.click(older)
    expect(older.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('heading', { name: 'Context size · #8' })).toBeTruthy()
    live = data([10, 9, 8])
    rerender(<MemoryRouter><LiveSessionSection /></MemoryRouter>)
    expect(within(screen.getByRole('list', { name: 'Request flow' })).getByRole('button', { name: /Request #10/ }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('heading', { name: 'Context size · #10' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '2 conversations' }))
    expect(screen.getAllByRole('region', { name: /Conversation [12]/ })).toHaveLength(2)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compact mode' }))
    expect(localStorage.getItem('contextspy.compact-request-cards')).toBe('false')
  })
})
