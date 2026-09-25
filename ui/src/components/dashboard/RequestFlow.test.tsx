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
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { DashboardRequestFlowItem } from '../../api/client'
import { RequestFlow } from './RequestFlow'

function item(overrides: Partial<DashboardRequestFlowItem> = {}): DashboardRequestFlowItem {
  return {
    id: 'request-18', session_seq: 18, timestamp: '2026-09-18T08:42:08', model: 'gpt-5.2', duration_ms: 1800,
    status_code: 200, invocation_outcome: 'completed', tokens_total_input: 87412, tokens_total_output: 612,
    ...overrides,
  }
}

function renderFlow(items: DashboardRequestFlowItem[]) {
  return render(<MemoryRouter><RequestFlow items={items} /></MemoryRouter>)
}

describe('RequestFlow', () => {
  it('keeps API order (newest first) and shows key values', () => {
    renderFlow([item(), item({ id: 'request-17', session_seq: 17, model: 'gpt-4' })])
    const cards = within(screen.getByRole('list')).getAllByRole('link')
    expect(cards[0].textContent).toContain('#18')
    expect(cards[1].textContent).toContain('#17')
    expect(cards[0].textContent).toContain('gpt-5.2 · 1.8s')
    expect(cards[0].textContent).toContain('87,412 in')
    expect(cards[0].textContent).toContain('612 out')
  })

  it('omits endpoint and HTTP method and links to the request', () => {
    renderFlow([item()])
    expect(screen.queryByText(/POST|GET|\/v1\//)).toBeNull()
    expect(screen.getByRole('link').getAttribute('href')).toBe('/requests/request-18')
  })

  it('falls back to a short id when the sequence is null', () => {
    renderFlow([item({ id: 'abcdef1234567890', session_seq: null })])
    expect(screen.getByRole('link').textContent).toContain('abcdef12')
  })

  it('exposes failed and incomplete states as text', () => {
    renderFlow([
      item({ id: 'a', invocation_outcome: 'failed', status_code: 500 }),
      item({ id: 'b', invocation_outcome: 'incomplete', status_code: null }),
    ])
    const [failed, incomplete] = screen.getAllByRole('link')
    expect(failed.getAttribute('aria-label')).toContain('Failed (500)')
    expect(incomplete.getAttribute('aria-label')).toContain('Incomplete')
  })

  it('shows an empty state', () => {
    renderFlow([])
    expect(screen.getByText(/No requests captured/)).toBeTruthy()
  })
})
