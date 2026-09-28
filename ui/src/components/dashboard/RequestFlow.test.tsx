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
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { useState } from 'react'
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

function renderFlow(items: Parameters<typeof RequestFlow>[0]['items']) {
  return render(<MemoryRouter><RequestFlow items={items} /></MemoryRouter>)
}

describe('RequestFlow', () => {
  it('keeps API order (newest first) and shows key values', () => {
    renderFlow([item(), item({ id: 'request-17', session_seq: 17, model: 'gpt-4' })])
    const cards = within(screen.getByRole('list')).getAllByRole('button')
    expect(cards[0].textContent).toContain('#18')
    expect(cards[1].textContent).toContain('#17')
    expect(cards[0].textContent).toContain('gpt-5.2 · 1.8s')
    expect(cards[0].textContent).toContain('87,412 in')
    expect(cards[0].textContent).toContain('612 out')
    expect(cards[0].parentElement?.className).toContain('w-36')
    expect(screen.getByRole('list').parentElement?.className).toContain('overflow-x-auto')
  })

  it('omits endpoint and HTTP method from the card', () => {
    renderFlow([item()])
    expect(screen.queryByText(/POST|GET|\/v1\//)).toBeNull()
    expect(screen.getByRole('button', { name: /Request #18/ })).toBeTruthy()
  })

  it('falls back to a short id when the sequence is null', () => {
    renderFlow([item({ id: 'abcdef1234567890', session_seq: null })])
    expect(screen.getByRole('button').textContent).toContain('abcdef12')
  })

  it('exposes failed and incomplete states as text', () => {
    renderFlow([
      item({ id: 'a', invocation_outcome: 'failed', status_code: 500 }),
      item({ id: 'b', invocation_outcome: 'incomplete', status_code: null }),
    ])
    const [failed, incomplete] = screen.getAllByRole('button')
    expect(failed.getAttribute('aria-label')).toContain('Failed (500)')
    expect(incomplete.getAttribute('aria-label')).toContain('Incomplete')
  })

  it('shows parent evidence in the card corner without splitting the request row', () => {
    renderFlow([
      { ...item(), lineage_relation: 'exact', parent_state: 'exact', parent_request_id: 'request-17', certainty: 'exact' },
      { ...item({ id: 'request-17', session_seq: 17 }), lineage_relation: 'context_affinity', parent_state: 'ambiguous', parent_request_id: null },
    ])
    expect(screen.getAllByRole('list')).toHaveLength(1)
    const exactIcon = screen.getByText('↳')
    fireEvent.mouseEnter(exactIcon)
    expect(screen.getByRole('tooltip').textContent).toContain('The provider explicitly linked this request to its predecessor.')
    expect(screen.getByRole('tooltip').parentElement).toBe(document.body)
    fireEvent.mouseLeave(exactIcon)
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.mouseEnter(screen.getByText('⋯'))
    expect(screen.getByRole('tooltip').textContent).toContain('its direct predecessor is unknown')
    expect(screen.getAllByRole('button')[1].getAttribute('aria-label')).toContain('Same stream; direct predecessor not established')
  })

  it('explains a display-only context-reset bridge without claiming a parent', () => {
    renderFlow([{ ...item(), lineage_relation: 'compaction_affinity', parent_state: 'ambiguous', parent_request_id: null }])
    fireEvent.mouseEnter(screen.getByText('⋯'))
    expect(screen.getByRole('tooltip').textContent).toContain('matching stream hint')
    const card = screen.getByRole('button', { name: /Same stream after context reset/ })
    expect(card.textContent).toContain('Same stream · direct predecessor not established')
    expect(card.textContent).not.toContain('Exact parent')
  })

  it('shows an empty state', () => {
    renderFlow([])
    expect(screen.getByText(/No requests captured/)).toBeTruthy()
  })

  it('selects on first activation and opens Request Detail on the second', () => {
    function Selectable() {
      const [selected, setSelected] = useState<string | null>(null)
      return <RequestFlow items={[item()]} selectedId={selected} onSelect={setSelected} />
    }
    render(<MemoryRouter initialEntries={['/']}><Routes>
      <Route path="/" element={<Selectable />} />
      <Route path="/requests/:id" element={<p>Request detail reached</p>} />
    </Routes></MemoryRouter>)
    const card = screen.getByRole('button', { name: /Request #18/ })
    fireEvent.click(card)
    expect(card.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(card)
    expect(screen.getByText('Request detail reached')).toBeTruthy()
  })

  it('keeps compact cards small and suppresses icon hover tooltips', () => {
    render(<MemoryRouter><RequestFlow items={[{ ...item(), lineage_relation: 'exact' }]} compact /></MemoryRouter>)
    expect(screen.getByRole('listitem').className).toContain('w-32')
    fireEvent.mouseEnter(screen.getByText('↳'))
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(screen.getByRole('button').getAttribute('aria-label')).toContain('Exact predecessor')
  })

  it('shows conversation code, seconds, and corrected arrows in three compact rows', () => {
    render(<MemoryRouter><RequestFlow items={[{ ...item({ conversation_code: 'C1', duration_ms: 250 }), lineage_relation: 'exact' }]}
      compact selectedId="request-18" onSelect={() => {}} /></MemoryRouter>)
    const card = screen.getByRole('button', { name: /Request #C1-18/ })
    expect(card.className).toContain('bg-[var(--surface-selected)]')
    expect(card.className).not.toContain('ring-2')
    expect(card.firstElementChild?.textContent).toContain('#C1-18')
    const rows = card.querySelectorAll('p')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('0.3s')
    expect(rows[1].textContent).toContain('↑ 87k')
    expect(rows[1].textContent).toContain('↓ 612')
  })

  it('labels auxiliary cards and uses the corrected arrows in detailed mode', () => {
    renderFlow([item({ conversation_code: 'AUX' })])
    const card = screen.getByRole('button', { name: /Request #AUX-18/ })
    expect(card.textContent).toContain('↑ 87,412 in')
    expect(card.textContent).toContain('↓ 612 out')
  })
})
