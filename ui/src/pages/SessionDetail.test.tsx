import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import SessionDetail from './SessionDetail'

vi.mock('../api/hooks', () => ({
  useSession: () => ({ isLoading: false, data: { session: { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00Z', ended_at: '2026-09-18T09:00:00Z', is_active: false } } }),
  useSessionLineage: (_id: string, enabled: boolean) => ({ isLoading: false, data: enabled ? { conversation_count: 1 } : undefined }),
  useSessionConversations: (_id: string, enabled: boolean) => ({ isLoading: false, data: enabled ? { conversation_count: 1 } : undefined }),
  useStatsSession: () => ({ data: undefined }),
  useTimeline: () => ({ data: undefined, isLoading: false }),
  useRequests: () => ({ data: undefined }),
  useToolStats: () => ({ data: undefined }),
  useEndSession: () => ({ mutate: vi.fn(), isPending: false }),
  useRenameSession: () => ({ mutate: vi.fn() }),
}))
vi.mock('../components/SessionConversationSequences', () => ({ SessionConversationSequences: () => <p>Sequence view</p> }))
vi.mock('../components/SessionLineage', () => ({ SessionLineage: () => <p>Diagnostic view</p> }))

function Location() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output data-testid="location">{location.search}</output><button onClick={() => navigate(-1)}>Browser back</button></>
}

describe('Session Detail conversation modes', () => {
  it('opens legacy lineage links as sequences and preserves unrelated parameters and history', async () => {
    render(<MemoryRouter initialEntries={['/sessions/s1?view=lineage&source=overview']}>
      <Routes><Route path="/sessions/:id" element={<><Location /><SessionDetail /></>} /></Routes>
    </MemoryRouter>)
    expect(screen.getByText('Sequence view')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('?view=lineage&source=overview')
    await userEvent.click(screen.getByRole('button', { name: 'Lineage diagnostics' }))
    expect(screen.getByText('Diagnostic view')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('?view=lineage&source=overview&mode=fragments')
    await userEvent.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.getByText('Sequence view')).toBeTruthy()
  })
})
