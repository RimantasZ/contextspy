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
  analysis_version: 'lineage-v1',
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

    expect(screen.getByRole('button', { name: 'Capture #1, Lineage root' }).textContent).toContain('Fork')
    expect(screen.getByRole('button', { name: /Inferred 91% from Capture #1 to Capture #3/ })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: /Exact continuation from Capture #1 to Capture #2/ }))
    expect(screen.getAllByText('Exact continuation').length).toBeGreaterThan(0)
    expect(screen.getByText('provider predecessor id')).toBeTruthy()
    expect(screen.getByText('2 blocks')).toBeTruthy()
    expect(screen.getByText('80 tokens')).toBeTruthy()
  })
})
