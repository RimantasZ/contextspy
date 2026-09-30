import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { DashboardRequestFlowItem } from '../../api/client'
import { RequestCard } from './RequestCard'

const item: DashboardRequestFlowItem = {
  id: 'r1', session_seq: 1, timestamp: '2026-09-18T08:42:08',
  model: 'test', duration_ms: 500, status_code: 503,
  invocation_outcome: 'failed', tokens_total_input: 100, tokens_total_output: 10,
}

describe('RequestCard as a standalone display primitive', () => {
  it('exposes selection, failure and activation without needing a page or router', () => {
    const onActivate = vi.fn()
    render(<RequestCard item={item} selected selectable compact onActivate={onActivate} />)
    const card = screen.getByRole('button', { name: /Request #1/ })
    expect(card.getAttribute('aria-pressed')).toBe('true')
    expect(card.getAttribute('aria-label')).toContain('Failed (503)')
    expect(card.querySelector('[class*="danger"]')).toBeTruthy()
    fireEvent.click(card)
    expect(onActivate).toHaveBeenCalledOnce()
  })

  it('explains lineage on keyboard focus only in detailed mode', () => {
    const linked = { ...item, lineage_relation: 'inferred' as const }
    const { rerender } = render(<RequestCard item={linked} onActivate={() => {}} />)
    const card = screen.getByRole('button')
    fireEvent.focus(card)
    expect(screen.getByRole('tooltip').textContent).toContain('inferred a direct predecessor')
    rerender(<RequestCard item={linked} compact onActivate={() => {}} />)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
