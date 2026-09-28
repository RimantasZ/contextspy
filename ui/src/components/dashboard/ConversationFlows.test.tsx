import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { DashboardConversation, DashboardLiveData } from '../../api/client'
import { ConversationFlows } from './ConversationFlows'

function card(id: string, seq: number): DashboardConversation['recent_segments'][number]['request_flow'][number] {
  return {
    id, session_seq: seq, timestamp: '2026-09-18T08:00:00', model: 'test', duration_ms: 10,
    status_code: 200, invocation_outcome: 'completed', tokens_total_input: 100, tokens_total_output: 5,
    parent_request_id: null, parent_state: 'root', certainty: null, confidence: null,
    lineage_relation: 'root', membership_state: 'unassigned', shared_history: false, fork_status: 'none',
  }
}

function group(label: string, seq: number, evidence: DashboardConversation['evidence'] = 'default'): DashboardConversation {
  const item = card(`r${seq}`, seq)
  return {
    key: label, label, evidence, fork_parent_request_id: null, latest_request_id: item.id,
    latest_session_seq: seq, latest_parent_state: 'root', request_count: 1, unlinked_segment_count: 0,
    recent_segments: [{ key: item.id, gap_reason: 'root', request_flow: [item] }],
    has_older_requests: false,
    context_change: { request_id: item.id, session_seq: seq, tokens_total_input: 100,
      parent_request_id: null, parent_session_seq: null, parent_state: 'root', parent_confidence: null,
      external_parent: false, first_conversation: false, token_delta: null,
      comparison_fidelity: 'unavailable', block_changes: [] },
  }
}

function show(groups: DashboardConversation[], auxiliary: DashboardConversation | null = null, onSelect = vi.fn()) {
  localStorage.removeItem('contextspy.conversation-density:s1')
  const data: DashboardLiveData = {
    active_session: { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00', request_count: 3,
      tokens_total_input: 300, tokens_total_output: 15 },
    request_flow: [], activity: [], context_change: null, conversations: groups,
    conversation_count: groups.length, auxiliary, auxiliary_request_count: auxiliary?.request_count ?? 0,
    confirmed_parallel_streams: 0, lineage_fragment_count: 0, has_more_conversations: false,
    most_recent_conversation_key: groups[0]?.key ?? null,
  }
  render(<MemoryRouter><ConversationFlows data={data} compact selectedId={null} onSelect={onSelect} onShowSequence={() => {}} /></MemoryRouter>)
  return onSelect
}

describe('grouped conversation rows', () => {
  it('keeps one row per group and explains its evidence beside the heading', () => {
    show([group('Conversation 1', 9), group('Conversation 2', 8, 'stream_affinity')])
    const regions = screen.getAllByRole('region', { name: /Conversation [12]/ })
    expect(regions).toHaveLength(2)
    expect(within(regions[1]).getByText('Supported separate stream · corroborated by context')).toBeTruthy()
    expect(within(regions[0]).getAllByRole('listitem')).toHaveLength(1)
    expect(regions[0].parentElement).toBe(regions[1].parentElement)
  })

  it('shows auxiliary requests after numbered conversations', () => {
    show([group('Conversation 1', 9)], group('Auxiliary requests', 8, 'auxiliary'))
    const regions = screen.getAllByRole('region').filter((node) =>
      ['Conversation 1', 'Auxiliary requests'].includes(node.getAttribute('aria-label') ?? ''))
    expect(regions.map((node) => node.getAttribute('aria-label'))).toEqual(['Conversation 1', 'Auxiliary requests'])
  })

  it('selects cards and retains the per-row total-count More link', () => {
    const conversation = group('Conversation 1', 23)
    conversation.request_count = 23
    conversation.has_older_requests = true
    const onSelect = show([conversation])
    fireEvent.click(screen.getByRole('button', { name: /Request #23/ }))
    expect(onSelect).toHaveBeenCalledWith('r23')
    expect(screen.getByRole('link', { name: 'More (23 total) →' }).getAttribute('href'))
      .toBe('/sessions/s1?view=lineage&conversation=Conversation%201')
  })

  it('expands conversations independently and remembers each choice', () => {
    localStorage.removeItem('contextspy.conversation-density:s1')
    show([group('Conversation 1', 9), group('Conversation 2', 8)])
    const first = screen.getByRole('region', { name: 'Conversation 1' })
    const second = screen.getByRole('region', { name: 'Conversation 2' })
    fireEvent.click(within(first).getByRole('button', { name: 'Detailed' }))
    expect(within(first).getByRole('button', { name: /Request #9/ }).textContent).toContain('100 in')
    expect(within(second).getByRole('button', { name: /Request #8/ }).textContent).not.toContain('100 in')
    expect(JSON.parse(localStorage.getItem('contextspy.conversation-density:s1') ?? '{}'))
      .toEqual({ 'Conversation 1': false })
  })
})
