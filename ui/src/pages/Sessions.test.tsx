import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { SessionSummaryEntry } from '../api/client'
import Sessions from './Sessions'

function entry(id: string, name: string, status: SessionSummaryEntry['status'], startedAt: string): SessionSummaryEntry {
  return {
    type: 'session', session_id: id, name, started_at: startedAt, ended_at: status === 'active' ? null : '2026-10-02T00:00:00Z',
    duration_ms: status === 'active' ? null : 3_600_000, is_active: status === 'active', status, archived_at: status === 'archived' ? '2026-10-05T00:00:00Z' : null,
    request_count: 4, tokens_in: 100, tokens_out: 10, tokens_system_prompt: 1, tokens_tool_definitions: 1, tokens_tool_results: 1,
    tokens_file_contents: 1, tokens_conversation_history: 1, tokens_current_user_message: 1, tokens_assistant_prefill: 0, tokens_uncategorized: 0,
  }
}

const entries = [
  entry('a', 'Active one', 'active', '2026-10-03T00:00:00Z'),
  entry('e', 'Ended one', 'ended', '2026-10-02T00:00:00Z'),
  entry('x', 'Archived one', 'archived', '2026-10-01T00:00:00Z'),
]

vi.mock('../api/hooks', () => ({
  useSessionsSummary: () => ({ isLoading: false, data: { entries } }),
  useRenameSession: () => ({ mutate: vi.fn() }),
  useDeleteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useArchiveSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
}))
vi.mock('../components/SessionControls', () => ({ SessionControls: () => null }))
vi.mock('../components/ContextBar', () => ({ ContextBar: () => null }))

function rowOf(name: string) {
  return screen.getByText(name).closest('tr') as HTMLElement
}

describe('Sessions list lifecycle', () => {
  it('shows a three-valued status badge', () => {
    render(<MemoryRouter><Sessions /></MemoryRouter>)
    expect(within(rowOf('Active one')).getByText('Active')).toBeTruthy()
    expect(within(rowOf('Ended one')).getByText('Ended')).toBeTruthy()
    expect(within(rowOf('Archived one')).getByText('Archived')).toBeTruthy()
  })

  it('offers Archive only for ended sessions', () => {
    render(<MemoryRouter><Sessions /></MemoryRouter>)
    expect(within(rowOf('Ended one')).getByRole('button', { name: 'Archive' })).toBeTruthy()
    expect(within(rowOf('Active one')).queryByRole('button', { name: 'Archive' })).toBeNull()
    expect(within(rowOf('Archived one')).queryByRole('button', { name: 'Archive' })).toBeNull()
  })

  it('opens the confirmation for the right session without navigating', async () => {
    render(<MemoryRouter><Sessions /></MemoryRouter>)
    await userEvent.click(within(rowOf('Ended one')).getByRole('button', { name: 'Archive' }))
    expect(screen.getByRole('dialog', { name: /Archive session .Ended one./ })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('sorts by lifecycle: archived, ended, active ascending and the reverse descending', async () => {
    render(<MemoryRouter><Sessions /></MemoryRouter>)
    const rowNames = () => screen.getAllByRole('row').slice(1).map((row) => ['Active one', 'Ended one', 'Archived one'].find((name) => row.textContent?.includes(name)))
    const header = screen.getByRole('columnheader', { name: /Status/ })
    await userEvent.click(header)
    expect(header.getAttribute('aria-sort')).toBe('ascending')
    expect(rowNames()).toEqual(['Archived one', 'Ended one', 'Active one'])
    await userEvent.click(header)
    expect(header.getAttribute('aria-sort')).toBe('descending')
    expect(rowNames()).toEqual(['Active one', 'Ended one', 'Archived one'])
  })
})
