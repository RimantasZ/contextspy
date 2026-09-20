// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Overview from './Dashboard'

const idle = { data: undefined, isLoading: false, isError: false }
let live: Record<string, unknown> = { ...idle, isLoading: true }

vi.mock('../api/hooks', () => ({
  useStatsOverview: () => idle,
  useRequests: () => idle,
  useToolStats: () => idle,
  useSessions: () => idle,
  useSessionsSummary: () => idle,
  useDashboardLive: () => live,
  useEndSession: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useCreateSession: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))

function renderOverview() {
  return render(<MemoryRouter><Overview /></MemoryRouter>)
}

describe('Overview', () => {
  it('places the live section between summary cards and Token composition, without header controls', () => {
    live = { data: { active_session: null, request_flow: [], activity: [], context_change: null }, isLoading: false, isError: false }
    renderOverview()
    const cards = screen.getByText('Providers')
    const liveSection = screen.getByRole('region', { name: 'Active session' })
    const composition = screen.getByText('Token composition')
    expect(cards.compareDocumentPosition(liveSection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(liveSection.compareDocumentPosition(composition) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Start session' }).length).toBe(1)
  })

  it('keeps existing sections when the live endpoint fails', () => {
    live = { data: undefined, isLoading: false, isError: true }
    renderOverview()
    expect(screen.getByText('Live session data could not be loaded.')).toBeTruthy()
    for (const text of ['Token composition', 'Sessions', 'Recent requests', 'Latency & Errors']) {
      expect(screen.getByText(text)).toBeTruthy()
    }
  })
})
