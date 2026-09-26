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
import { describe, expect, it } from 'vitest'
import type { DashboardBlockChange, DashboardContextChange } from '../../api/client'
import { ContextChangePanel } from './ContextChangePanel'

function bc(block_type: string, delta: number): DashboardBlockChange {
  return { block_type, current_count: Math.max(delta, 0), previous_count: 0, delta }
}

function change(overrides: Partial<DashboardContextChange> = {}): DashboardContextChange {
  return {
    request_id: 'r18', session_seq: 18, tokens_total_input: 87412, previous_request_id: 'r17', previous_session_seq: 17,
    token_delta: 5612, comparison_fidelity: 'complete', block_changes: [], ...overrides,
  }
}

describe('ContextChangePanel', () => {
  it('shows size, comparison sequence and positive delta', () => {
    render(<ContextChangePanel change={change()} />)
    expect(screen.getByText('87,412')).toBeTruthy()
    expect(screen.getByText('Latest request · compared with #17')).toBeTruthy()
    expect(screen.getByText(/\+5,612 tokens/)).toBeTruthy()
  })

  it('shows negative and zero deltas', () => {
    const { unmount } = render(<ContextChangePanel change={change({ token_delta: -840 })} />)
    expect(screen.getByText(/−840 tokens/)).toBeTruthy()
    unmount()
    render(<ContextChangePanel change={change({ token_delta: 0 })} />)
    expect(screen.getByText(/^0 tokens/)).toBeTruthy()
  })

  it('orders and labels block changes and omits zero deltas', () => {
    render(<ContextChangePanel change={change({
      block_changes: [bc('tool_result', 2), bc('user_message', 1), bc('tool_call', 2), bc('thinking', 0), bc('system_prompt', -1)],
    })} />)
    const items = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(items).toEqual(['Tool calls+2', 'User messages+1', 'Tool results+2', 'System prompts−1'])
    expect(screen.queryByText('Thinking blocks')).toBeNull()
  })

  it('summarizes more than four changed types', () => {
    render(<ContextChangePanel change={change({
      block_changes: [bc('tool_call', 1), bc('user_message', 1), bc('tool_result', 1), bc('assistant_message', 1), bc('thinking', 1), bc('other', 1)],
    })} />)
    expect(screen.getAllByRole('listitem').length).toBe(5)
    expect(screen.getByText('2 other block types changed')).toBeTruthy()
  })

  it('handles no changes, partial, unavailable and first-request states', () => {
    const { unmount } = render(<ContextChangePanel change={change({ block_changes: [bc('tool_call', 0)] })} />)
    expect(screen.getByText('No block-count changes.')).toBeTruthy()
    unmount()

    const partial = render(<ContextChangePanel change={change({ comparison_fidelity: 'partial', block_changes: [bc('tool_call', 1)] })} />)
    expect(screen.getByText('Partial capture')).toBeTruthy()
    partial.unmount()

    const unavailable = render(<ContextChangePanel change={change({ comparison_fidelity: 'unavailable' })} />)
    expect(screen.getByText('Block comparison unavailable.')).toBeTruthy()
    unavailable.unmount()

    render(<ContextChangePanel change={change({
      previous_request_id: null, previous_session_seq: null, token_delta: null, comparison_fidelity: 'unavailable',
    })} />)
    expect(screen.getByText('First request in session.')).toBeTruthy()
    expect(screen.queryByText('Block changes')).toBeNull()
  })

  it('never shows a limit, percentage or progress bar', () => {
    const { container } = render(<ContextChangePanel change={change()} />)
    expect(container.textContent).not.toMatch(/%|limit|remaining|max/i)
    expect(container.querySelector('[role="progressbar"], progress, meter')).toBeNull()
  })
})
