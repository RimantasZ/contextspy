import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { DashboardConversation, DashboardLiveData } from '../../api/client'
import { LiveSessionSection } from './LiveSessionSection'

let live: DashboardLiveData
vi.mock('../../api/hooks', () => ({
  useDashboardLive: () => ({ data: live, isLoading: false, isError: false }),
  useEndSession: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))

function card(id: string, seq: number, parent: string | null = null): DashboardConversation['recent_segments'][number]['request_flow'][number] {
  return {
    id, session_seq: seq, timestamp: `2026-09-18T08:${String(seq).padStart(2, '0')}:00`,
    model: 'gpt-test', duration_ms: 500, status_code: 200, invocation_outcome: 'completed',
    tokens_total_input: 100 + seq, tokens_total_output: 10,
    parent_request_id: parent, parent_state: parent ? 'exact' : 'ambiguous',
    certainty: parent ? 'exact' : null, confidence: null, membership_state: 'unassigned', shared_history: false,
  }
}

function group(key: string, seq: number, cards: ReturnType<typeof card>[], evidence: DashboardConversation['evidence'] = 'default'): DashboardConversation {
  return {
    key, label: key, evidence, fork_parent_request_id: evidence === 'fork' ? 'root' : null,
    latest_request_id: cards[0].id, latest_session_seq: seq, latest_parent_state: cards[0].parent_state,
    request_count: cards.length, unlinked_segment_count: cards.length - 1,
    recent_segments: cards.map((item) => ({ key: item.id, gap_reason: item.parent_state, request_flow: [item] })),
    has_older_requests: false,
    context_change: {
      request_id: cards[0].id, session_seq: seq, tokens_total_input: cards[0].tokens_total_input,
      parent_request_id: cards[0].parent_request_id, parent_session_seq: null,
      parent_state: cards[0].parent_state, parent_confidence: null, external_parent: false,
      first_conversation: false, token_delta: null, comparison_fidelity: 'unavailable', block_changes: [],
    },
  }
}

function show(groups: DashboardConversation[], hidden = false) {
  live = {
    active_session: { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00', request_count: 4, tokens_total_input: 420, tokens_total_output: 40 },
    request_flow: [], activity: [], context_change: groups[0]?.context_change ?? null,
    conversations: groups, conversation_count: groups.length + Number(hidden),
    confirmed_parallel_streams: Math.max(0, groups.length - 1), lineage_fragment_count: 4,
    has_more_conversations: hidden, most_recent_conversation_key: groups[groups.length - 1]?.key ?? null,
  }
  return render(<MemoryRouter><LiveSessionSection /></MemoryRouter>)
}

describe('conversation-aware live session', () => {
  it('keeps uncertain fragments in one sequence with marked breaks', () => {
    show([group('Session request sequence', 4, [card('r4', 4), card('r2', 2)])])
    expect(screen.getAllByText(/Lineage gap · ambiguous parent/)).toHaveLength(2)
    const region = screen.getByRole('region', { name: 'Session request sequence' })
    expect(within(region).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual(['/requests/r4', '/requests/r2'])
    expect(screen.queryByText(/Conversation 2/)).toBeNull()
  })

  it('shows confirmed streams separately and changes the selected context', async () => {
    const primary = group('Primary conversation', 2, [card('a2', 2, 'a1')], 'fork')
    const second = group('Conversation 2', 4, [card('b4', 4, 'root')], 'fork')
    show([primary, second], true)
    expect(screen.getAllByText(/Confirmed fork from root/)).toHaveLength(2)
    expect(screen.getByRole('link', { name: /View all 3 conversations/ }).getAttribute('href')).toBe('/sessions/s1?view=lineage')
    expect(screen.getByText('104')).toBeTruthy()
    await userEvent.click(within(screen.getByRole('region', { name: 'Primary conversation' })).getByRole('button', { name: 'Show context' }))
    expect(screen.getByText('102')).toBeTruthy()
  })
})
