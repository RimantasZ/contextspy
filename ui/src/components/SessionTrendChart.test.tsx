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
    data, metricId: 'context_estimated', onMetricChange: vi.fn(), ...overrides,
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

  it('opens the request under the pointer when its point is clicked', () => {
    const onPointClick = vi.fn()
    const { container } = show({ onPointClick })
    const wrapper = container.querySelector('.recharts-wrapper') as HTMLElement
    fireEvent.mouseMove(wrapper, { clientX: 590, clientY: 100 })
    const dots = container.querySelectorAll('.recharts-active-dot circle')
    expect(dots.length).toBeGreaterThan(0)
    fireEvent.click(dots[0])
    expect(onPointClick).toHaveBeenCalledWith('r2')
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

  it('has no x-axis toggle and no pan controls for a short session', () => {
    show()
    expect(screen.queryByRole('button', { name: 'Time' })).toBeNull()
    expect(screen.queryByRole('slider', { name: 'Pan through requests' })).toBeNull()
  })

  describe('panning a long session', () => {
    const longData = (count: number): SessionTrend => ({
      ...data,
      series: [{
        key: 'c1', label: 'Conversation 1', auxiliary: false, request_count: count,
        points: Array.from({ length: count }, (_, i) => point(i + 1, {})),
      }],
    })

    it('shows the latest 100 requests and follows new ones', () => {
      const { rerender, props } = show({ data: longData(250) })
      expect(screen.getByText('#151–#250 of 250')).toBeTruthy()
      rerender(<SessionTrendChart {...props} data={longData(260)} />)
      expect(screen.getByText('#161–#260 of 260')).toBeTruthy()
    })

    it('pans back with the step buttons and returns with Latest', () => {
      show({ data: longData(250) })
      fireEvent.click(screen.getByRole('button', { name: 'Show earlier requests' }))
      expect(screen.getByText('#126–#225 of 250')).toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: 'Latest' }))
      expect(screen.getByText('#151–#250 of 250')).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Latest' })).toBeNull()
    })

    it('pans with the slider and holds the view when new requests arrive', () => {
      const { rerender, props } = show({ data: longData(250) })
      fireEvent.change(screen.getByRole('slider', { name: 'Pan through requests' }), { target: { value: '10' } })
      expect(screen.getByText('#11–#110 of 250')).toBeTruthy()
      rerender(<SessionTrendChart {...props} data={longData(260)} />)
      expect(screen.getByText('#11–#110 of 260')).toBeTruthy()
    })

    it('stops at the oldest request', () => {
      show({ data: longData(120) })
      fireEvent.click(screen.getByRole('button', { name: 'Show earlier requests' }))
      expect(screen.getByText('#1–#100 of 120')).toBeTruthy()
      expect((screen.getByRole('button', { name: 'Show earlier requests' }) as HTMLButtonElement).disabled).toBe(true)
    })
  })
})
