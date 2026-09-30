import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { LineageGraph, LineageNode } from '../api/client'
import { SessionLineage } from './SessionLineage'

vi.mock('../api/hooks', () => ({
  useContextDiff: () => ({ data: undefined }),
}))

function node(overrides: Partial<LineageNode>): LineageNode {
  return {
    request_id: 'root',
    session_id: 'capture-1',
    session_seq: 1,
    started_at: '2026-09-11T12:00:00Z',
    started_at_source: 'observed',
    completed_at: '2026-09-11T12:00:01Z',
    duration_ms: 1_000,
    provider: 'openai',
    agent: 'codex',
    model: 'gpt-test',
    endpoint: '/v1/responses',
    context_fidelity: 'complete',
    tokens_total_input: 100,
    tokens_total_output: 20,
    provider_response_id: 'response-root',
    predecessor_response_id: null,
    external: false,
    parent_state: 'root',
    lineage_key: 'root',
    lineage_number: 1,
    depth: 0,
    branch: 0,
    is_fork: true,
    conversation_fork_status: 'confirmed',
    parent_request_id: null,
    conversation_membership: [{ key: 'session:capture-1:primary', state: 'confirmed' }, { key: 'session:capture-1:fork:inferred-child', state: 'confirmed' }],
    ...overrides,
  }
}

const summary = {
  persisted: { blocks: 2, tokens: 80, by_category: {}, by_block_type: {} },
  promoted: { blocks: 1, tokens: 20, by_category: {}, by_block_type: {} },
  added: { blocks: 1, tokens: 5, by_category: {}, by_block_type: {} },
  removed: { blocks: 0, tokens: 0, by_category: {}, by_block_type: {} },
  replaced: { blocks: 0, tokens_before: 0, tokens_after: 0 },
}

const graph: LineageGraph = {
  capture: {
    id: 'capture-1',
    name: 'Parallel work',
    started_at: '2026-09-11T12:00:00Z',
    ended_at: null,
    is_active: true,
  },
  analysis_version: 'lineage-v2',
  conversation_count: 2,
  confirmed_parallel_streams: 1,
  lineage_fragment_count: 2,
  lineage_paths: [
    { key: 'exact-child', request_ids: ['root', 'exact-child'], leaf_request_id: 'exact-child', root_request_id: 'root', root_session_seq: 1, leaf_session_seq: 2, request_count: 2, last_activity: '2026-09-11T12:00:03Z', start_parent_state: 'root' },
    { key: 'inferred-child', request_ids: ['root', 'inferred-child'], leaf_request_id: 'inferred-child', root_request_id: 'root', root_session_seq: 1, leaf_session_seq: 3, request_count: 2, last_activity: '2026-09-11T12:00:04Z', start_parent_state: 'root' },
  ],
  conversations: [
    { key: 'session:capture-1:primary', label: 'Primary conversation', evidence: 'fork', fork_parent_request_id: 'root', request_ids: ['root', 'exact-child'], confirmed_request_ids: ['root', 'exact-child'] },
    { key: 'session:capture-1:fork:inferred-child', label: 'Conversation 2', evidence: 'fork', fork_parent_request_id: 'root', request_ids: ['root', 'inferred-child'], confirmed_request_ids: ['root', 'inferred-child'] },
  ],
  stream_bridges: {},
  nodes: [
    node({}),
    node({
      request_id: 'exact-child',
      session_seq: 2,
      started_at: '2026-09-11T12:00:02Z',
      completed_at: '2026-09-11T12:00:03Z',
      provider_response_id: 'response-2',
      predecessor_response_id: 'response-root',
      parent_state: 'exact',
      depth: 1,
      is_fork: false,
    }),
    node({
      request_id: 'inferred-child',
      session_seq: 3,
      started_at: '2026-09-11T12:00:02.250Z',
      completed_at: '2026-09-11T12:00:04Z',
      provider_response_id: null,
      parent_state: 'inferred',
      depth: 1,
      branch: 1,
      is_fork: false,
    }),
  ],
  edges: [
    {
      source_request_id: 'root',
      target_request_id: 'exact-child',
      relation_type: 'context_continuation',
      certainty: 'exact',
      confidence: null,
      evidence_source: 'provider',
      evidence: { reason_codes: ['provider_predecessor_id'] },
      delta: { summary },
      external_source: false,
    },
    {
      source_request_id: 'root',
      target_request_id: 'inferred-child',
      relation_type: 'context_continuation',
      certainty: 'inferred',
      confidence: 0.91,
      evidence_source: 'context_diff',
      evidence: { reason_codes: ['ordered_context_retained'] },
      delta: { summary },
      external_source: false,
    },
  ],
  unresolved_predecessors: [],
  ambiguous_candidates: [],
}

describe('SessionLineage', () => {
  it('renders forks and exposes edge evidence and context change totals', async () => {
    render(<MemoryRouter><SessionLineage graph={graph} /></MemoryRouter>)

    expect(screen.getByRole('button', { name: 'Request #1, No known parent' }).textContent).toContain('Confirmed fork')
    expect(screen.getByRole('button', { name: /Inferred score 91\/100 from Request #1 to Request #3/ })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Request #1, No known parent' }))
    expect(screen.getByText('Primary conversation, Conversation 2 · request depth 0')).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: /#1 → #2/ }))
    await userEvent.click(screen.getByRole('button', { name: /Exact continuation from Request #1 to Request #2/ }))
    expect(screen.getAllByText('Exact continuation').length).toBeGreaterThan(0)
    expect(screen.getByText('provider predecessor id')).toBeTruthy()
    expect(screen.getByText('2 blocks')).toBeTruthy()
    expect(screen.getByText('80 tokens')).toBeTruthy()
  })

  it('labels a structural branch without claiming a confirmed conversation split', async () => {
    const unconfirmed: LineageGraph = {
      ...graph,
      conversation_count: 1,
      confirmed_parallel_streams: 0,
      nodes: graph.nodes.map((item) => item.request_id === 'root'
        ? { ...item, conversation_fork_status: 'unconfirmed_graph_branch' }
        : item),
    }
    render(<MemoryRouter><SessionLineage graph={unconfirmed} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: 'Request #1, No known parent' }).textContent).toContain('Graph branch')
    await userEvent.click(screen.getByRole('button', { name: 'Request #1, No known parent' }))
    expect(screen.getByText('Graph branch · conversation split unconfirmed')).toBeTruthy()
    expect(screen.getByLabelText('Lineage graph')).toBeTruthy()
  })

  it('keeps long diagnostic paths in bounded windows', async () => {
    const ids = Array.from({ length: 30 }, (_, index) => `long-${index + 1}`)
    const longGraph: LineageGraph = {
      ...graph,
      lineage_paths: [{ key: 'long-30', request_ids: ids, leaf_request_id: 'long-30', root_request_id: 'long-1', root_session_seq: 1,
        leaf_session_seq: 30, request_count: 30, last_activity: '2026-09-11T12:00:30Z', start_parent_state: 'root' }],
      nodes: ids.map((id, index) => node({ request_id: id, session_seq: index + 1, depth: index, is_fork: false,
        conversation_fork_status: 'none', started_at: `2026-09-11T12:00:${String(index).padStart(2, '0')}Z`,
        completed_at: `2026-09-11T12:00:${String(index + 1).padStart(2, '0')}Z` })),
      edges: [],
    }
    render(<MemoryRouter><SessionLineage graph={longGraph} /></MemoryRouter>)
    expect(screen.getAllByRole('button', { name: /^Request #\d+,/ })).toHaveLength(25)
    await userEvent.click(screen.getByRole('button', { name: 'Earlier nodes' }))
    expect(screen.getAllByRole('button', { name: /^Request #\d+,/ })).toHaveLength(5)
    await userEvent.click(screen.getByRole('button', { name: 'Newer nodes' }))
    expect(screen.getAllByRole('button', { name: /^Request #\d+,/ })).toHaveLength(25)
  })

  it('labels an ambiguity candidate outside the visible window by its own request number', async () => {
    const ids = Array.from({ length: 30 }, (_, index) => `long-${index + 1}`)
    const longGraph: LineageGraph = {
      ...graph,
      lineage_paths: [{ key: 'long-30', request_ids: ids, leaf_request_id: 'long-30', root_request_id: 'long-1', root_session_seq: 1,
        leaf_session_seq: 30, request_count: 30, last_activity: '2026-09-11T12:00:30Z', start_parent_state: 'root' }],
      nodes: ids.map((id, index) => node({ request_id: id, session_seq: index + 1, depth: index, is_fork: false,
        conversation_fork_status: 'none', parent_state: index === 29 ? 'ambiguous' : 'root' })),
      edges: [],
      ambiguous_candidates: [{ request_id: 'long-30', candidates: [
        { request_id: 'long-1', confidence: 0.72, evidence: {} },
      ] }],
    }
    render(<MemoryRouter><SessionLineage graph={longGraph} /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: 'Request #30, Ambiguous parent' }))
    expect(screen.getByRole('button', { name: 'Request #1 · score 72/100' })).toBeTruthy()
  })
})
