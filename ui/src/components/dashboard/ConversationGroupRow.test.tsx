import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ConversationGroupRow } from './ConversationGroupRow'

describe('ConversationGroupRow outside Dashboard and Session Detail', () => {
  it('renders evidence and caller-owned slots without assuming a query or page', () => {
    const onCompactChange = vi.fn()
    render(<MemoryRouter><ConversationGroupRow
      group={{ key: 'group-9', label: 'Conversation 9', evidence: 'sustained_chain', fork_parent_request_id: null }}
      items={[{
        id: 'r9', session_seq: 9, timestamp: '2026-09-18T08:42:08',
        model: 'test', duration_ms: 500, status_code: 200,
        invocation_outcome: 'completed', tokens_total_input: 100, tokens_total_output: 10,
      }]} compact onCompactChange={onCompactChange}
      selectedId={null} onSelect={() => {}}
      meta={<span>Third-host metadata</span>}
      beforeFlow={<p>Before sequence</p>}
      trailingAction={<button type="button">More cards</button>}
    /></MemoryRouter>)
    expect(screen.getByRole('region', { name: 'Conversation 9' })).toBeTruthy()
    expect(screen.getByText(/Sustained chain/)).toBeTruthy()
    expect(screen.getByText('Third-host metadata')).toBeTruthy()
    expect(screen.getByText('Before sequence')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'More cards' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Detailed' }))
    expect(onCompactChange).toHaveBeenCalledWith(false)
  })
})
