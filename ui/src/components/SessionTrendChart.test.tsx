import { cloneElement } from 'react'
import type { ReactElement } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SessionTrend, TrendPoint } from '../api/client'
import { SessionTrendChart } from './SessionTrendChart'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children, { width: 600, height: 220 } as object),
  }
})

function point(seq: number, values: Partial<Record<string, number | null>>, fidelity = 'complete'): TrendPoint {
  return {
    request_id: `r${seq}`, session_seq: seq, ordinal: seq, time: `2026-09-18T08:0${seq}:00`,
    context_fidelity: fidelity, purpose: 'main',
    values: { context_estimated: 1000 * seq, cache_hit_pct: null, ttft_ms: null, duration_ms: null, ...values },
  }
}

const data: SessionTrend = {
  session_id: 's1',
  metrics: [
    { id: 'context_estimated', label: 'Context size (estimated)', unit: 'tokens', estimated: true, empty_hint: null },
    { id: 'cache_hit_pct', label: 'Cache hit %', unit: 'percent', estimated: false, empty_hint: 'Provider did not report cache usage' },
  ],
  series: [
    { key: 'c1', label: 'Conversation 1', auxiliary: false, request_count: 2, points: [point(1, {}), point(2, {}, 'partial')] },
    { key: 'aux', label: 'Auxiliary requests', auxiliary: true, request_count: 1, points: [point(3, {})] },
  ],
}

function show(overrides: Partial<React.ComponentProps<typeof SessionTrendChart>> = {}) {
  const props = {
    data, metricId: 'context_estimated', onMetricChange: vi.fn(), xMode: 'request' as const,
    onXModeChange: vi.fn(), ...overrides,
  }
  const view = render(<SessionTrendChart {...props} />)
  return { ...view, props }
}

describe('SessionTrendChart', () => {
  it('lists the server-provided metrics and reports a change', () => {
    const { props } = show()
    const select = screen.getByRole('combobox', { name: 'Trend metric' }) as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['Context size (estimated)', 'Cache hit %'])
    fireEvent.change(select, { target: { value: 'cache_hit_pct' } })
    expect(props.onMetricChange).toHaveBeenCalledWith('cache_hit_pct')
  })

  it('falls back to the first metric for an unknown metric id', () => {
    show({ metricId: 'bogus' })
    expect((screen.getByRole('combobox', { name: 'Trend metric' }) as HTMLSelectElement).value).toBe('context_estimated')
  })

  it('switches the x axis and marks the active mode', () => {
    const { props } = show()
    expect(screen.getByRole('button', { name: 'Request #' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Time' }).getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: 'Time' }))
    expect(props.onXModeChange).toHaveBeenCalledWith('time')
  })

  it('shows conversations on and auxiliary off by default, and toggles them', () => {
    const { container } = show()
    const conversation = screen.getByRole('checkbox', { name: /Conversation 1/ }) as HTMLInputElement
    const aux = screen.getByRole('checkbox', { name: /Auxiliary requests/ }) as HTMLInputElement
    expect(conversation.checked).toBe(true)
    expect(aux.checked).toBe(false)
    expect(container.querySelectorAll('.recharts-line')).toHaveLength(1)
    fireEvent.click(aux)
    expect(container.querySelectorAll('.recharts-line')).toHaveLength(2)
    fireEvent.click(conversation)
    fireEvent.click(aux)
    expect(screen.getByText('No conversation selected')).toBeTruthy()
  })

  it('draws a hollow dot for a request with partial context', () => {
    const { container } = show()
    const hollow = Array.from(container.querySelectorAll('circle')).filter(
      (c) => c.getAttribute('fill') === 'var(--chart-tooltip)',
    )
    expect(hollow).toHaveLength(1)
  })

  it('shows the metric empty hint when no plotted point has a value', () => {
    show({ metricId: 'cache_hit_pct' })
    expect(screen.getByText('Provider did not report cache usage')).toBeTruthy()
  })

  it('shows loading and empty states', () => {
    const { unmount } = show({ loading: true })
    expect(screen.getByText('Loading…')).toBeTruthy()
    unmount()
    show({ data: { ...data, series: [] } })
    expect(screen.getByText('No data yet')).toBeTruthy()
  })
})
