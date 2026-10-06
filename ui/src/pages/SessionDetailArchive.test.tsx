import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SessionDetail from './SessionDetail'

const state = vi.hoisted(() => ({ session: {} as Record<string, unknown> }))

vi.mock('../api/hooks', () => ({
  useSession: () => ({ isLoading: false, data: { session: state.session } }),
  useSessionLineage: () => ({ isLoading: false, data: undefined }),
  useSessionConversations: () => ({ isLoading: false, data: undefined }),
  useStatsSession: () => ({ data: undefined }),
  useTimeline: () => ({ data: undefined, isLoading: false }),
  useRequests: () => ({ data: undefined }),
  useToolStats: () => ({ data: undefined }),
  useEndSession: () => ({ mutate: vi.fn(), isPending: false }),
  useRenameSession: () => ({ mutate: vi.fn() }),
  useDeleteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useArchiveSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
}))
vi.mock('../components/SessionConversationSequences', () => ({ SessionConversationSequences: () => null }))
vi.mock('../components/SessionLineage', () => ({ SessionLineage: () => null }))

const base = { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00Z' }

function renderPage() {
  render(<MemoryRouter initialEntries={['/sessions/s1']}><Routes><Route path="/sessions/:id" element={<SessionDetail />} /></Routes></MemoryRouter>)
}

beforeEach(() => { state.session = {} })

describe('Session detail archive action', () => {
  it('is disabled with an explanation while the session is active', () => {
    state.session = { ...base, ended_at: null, is_active: true, status: 'active', archived_at: null }
    renderPage()
    const button = screen.getByRole('button', { name: 'Archive' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.title).toBe('End the session before archiving it')
    expect(screen.queryByText('Archived')).toBeNull()
  })

  it('is enabled for an ended session and opens the confirmation', async () => {
    state.session = { ...base, ended_at: '2026-09-18T09:00:00Z', is_active: false, status: 'ended', archived_at: null }
    renderPage()
    const button = screen.getByRole('button', { name: 'Archive' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    await userEvent.click(button)
    expect(screen.getByRole('dialog', { name: /Archive session .Work./ })).toBeTruthy()
  })

  it('is replaced by an Archived badge once archived, and End session stays hidden', () => {
    state.session = { ...base, ended_at: '2026-09-18T09:00:00Z', is_active: false, status: 'archived', archived_at: '2026-10-05T10:00:00Z' }
    renderPage()
    expect(screen.queryByRole('button', { name: 'Archive' })).toBeNull()
    expect(screen.getByText('Archived')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'End session' })).toBeNull()
  })
})
