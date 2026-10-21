import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import SessionDetail from './SessionDetail'

vi.mock('../api/hooks', () => ({
  useSession: () => ({ isLoading: false, data: { session: { id: 's1', name: 'Work', started_at: '2026-09-18T08:00:00Z', ended_at: '2026-09-18T09:00:00Z', is_active: false, status: 'ended', archived_at: null } } }),
  useSessionLineage: (_id: string, enabled: boolean) => ({ isLoading: false, data: enabled ? { conversation_count: 1 } : undefined }),
  useSessionConversations: (_id: string, enabled: boolean) => ({ isLoading: false, data: enabled ? { conversation_count: 1 } : undefined }),
  useStatsSession: () => ({ data: undefined }),
  useTimeline: () => ({ data: undefined, isLoading: false }),
  useSessionTrend: () => ({ data: undefined, isLoading: false }),
  useRequests: () => ({ data: undefined }),
  useToolStats: () => ({ data: undefined }),
  useEndSession: () => ({ mutate: vi.fn(), isPending: false }),
  useRenameSession: () => ({ mutate: vi.fn() }),
  useDeleteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useArchiveSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
}))
vi.mock('../components/SessionConversationSequences', () => ({ SessionConversationSequences: () => <p>Sequence view</p> }))
vi.mock('../components/hotspots/HotSpots', () => ({
  HotSpots: ({ sessionId }: { sessionId: string }) => <p>Hot spots of {sessionId}</p>,
  HOTSPOT_URL_PARAMS: ['group', 'scope', 'conversation', 'sort', 'category', 'block_type', 'source', 'in_context'],
}))
vi.mock('../components/SessionTrendChart', () => ({
  SessionTrendChart: ({ metricId, onMetricChange }: { metricId: string; onMetricChange: (id: string) => void }) => (
    <><output data-testid="trend-metric">{metricId}</output><button onClick={() => onMetricChange('ttft_ms')}>Pick TTFT</button></>
  ),
}))
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

  it('offers a Hot spots view that owns its parameters and clears them when leaving', async () => {
    render(<MemoryRouter initialEntries={['/sessions/s1?view=lineage&mode=fragments&unrelated=1']}>
      <Routes><Route path="/sessions/:id" element={<><Location /><SessionDetail /></>} /></Routes>
    </MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: 'Hot spots' }))
    expect(screen.getByText('Hot spots of s1')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('?view=hotspots&unrelated=1')
    await userEvent.click(screen.getByRole('button', { name: 'Hot spots' }))  // already selected: nothing changes
    expect(screen.getByTestId('location').textContent).toBe('?view=hotspots&unrelated=1')
    await userEvent.click(screen.getByRole('button', { name: 'Summary' }))
    expect(screen.queryByText('Hot spots of s1')).toBeNull()
    expect(screen.getByTestId('location').textContent).toBe('?unrelated=1')
  })

  it('opens hot spots straight from a link with its parameters', () => {
    render(<MemoryRouter initialEntries={['/sessions/s1?view=hotspots&conversation=c1&group=file']}>
      <Routes><Route path="/sessions/:id" element={<SessionDetail />} /></Routes>
    </MemoryRouter>)
    expect(screen.getByText('Hot spots of s1')).toBeTruthy()
  })

  it('reads the trend metric from the URL and writes it back without touching other parameters or history', async () => {
    render(<MemoryRouter initialEntries={['/sessions/s1?before=1', '/sessions/s1?metric=cache_hit_pct&unrelated=1']} initialIndex={1}>
      <Routes><Route path="/sessions/:id" element={<><Location /><SessionDetail /></>} /></Routes>
    </MemoryRouter>)
    expect(screen.getByTestId('trend-metric').textContent).toBe('cache_hit_pct')
    await userEvent.click(screen.getByRole('button', { name: 'Pick TTFT' }))
    expect(screen.getByTestId('trend-metric').textContent).toBe('ttft_ms')
    expect(screen.getByTestId('location').textContent).toBe('?metric=ttft_ms&unrelated=1')
    // the metric change replaces the history entry instead of adding one
    await userEvent.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.getByTestId('location').textContent).toBe('?before=1')
  })

  it('passes an empty metric to the chart when the URL has none, so it picks its default', () => {
    render(<MemoryRouter initialEntries={['/sessions/s1']}>
      <Routes><Route path="/sessions/:id" element={<SessionDetail />} /></Routes>
    </MemoryRouter>)
    expect(screen.getByTestId('trend-metric').textContent).toBe('')
  })
})
