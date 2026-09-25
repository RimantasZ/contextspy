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
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import type { DashboardActiveSession } from '../../api/client'
import { ActiveSessionPanel } from './ActiveSessionPanel'

const mutate = vi.fn()
let pending = false

vi.mock('../../api/hooks', () => ({
  useEndSession: () => ({ mutate, isPending: pending, isError: false }),
  useCreateSession: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))

const session: DashboardActiveSession = {
  id: 's1',
  name: 'A very long session name that should be truncated visually',
  started_at: '2026-01-01T00:00:00.000Z',
  request_count: 18,
  tokens_total_input: 318420,
  tokens_total_output: 5921,
}

function renderPanel(value: DashboardActiveSession | null) {
  return render(<MemoryRouter><ActiveSessionPanel session={value} /></MemoryRouter>)
}

describe('ActiveSessionPanel', () => {
  beforeAll(() => {
    vi.spyOn(Date, 'now').mockReturnValue(new Date('2026-01-01T00:05:00.000Z').getTime())
  })

  afterAll(() => {
    vi.restoreAllMocks()
  })

  it('shows name, elapsed time, counts and totals', () => {
    renderPanel(session)
    expect(screen.getByText('Active session')).toBeTruthy()
    expect(screen.getByText('5m 0s')).toBeTruthy()
    expect(screen.getByText('18')).toBeTruthy()
    expect(screen.getByText('318,420')).toBeTruthy()
    expect(screen.getByText('5,921')).toBeTruthy()
  })

  it('links the name to session detail and keeps it accessible via title', () => {
    renderPanel(session)
    const link = screen.getByRole('link', { name: session.name })
    expect(link.getAttribute('href')).toBe('/sessions/s1')
    expect(link.getAttribute('title')).toBe(session.name)
  })

  it('ends the session once and disables while pending', async () => {
    pending = false
    const { unmount } = renderPanel(session)
    await userEvent.click(screen.getByRole('button', { name: 'End session' }))
    expect(mutate).toHaveBeenCalledTimes(1)
    expect(mutate).toHaveBeenCalledWith('s1')
    unmount()

    pending = true
    renderPanel(session)
    expect((screen.getByRole('button', { name: 'End session' }) as HTMLButtonElement).disabled).toBe(true)
    pending = false
  })

  it('opens the shared start dialog when no session is active', async () => {
    renderPanel(null)
    expect(screen.getByText('No active session')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(screen.getByRole('dialog', { name: 'Start a session' })).toBeTruthy()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
