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
    // 3 parts: system 100, tools 200 x seq, history the rest
    parts: { context_composition: { system_prompt: 100, tool_results: 200 * seq, conversation_history: 700 * seq - 100 } },
  }
}

const data: SessionTrend = {
  session_id: 's1',
  metrics: [
    { id: 'context_estimated', label: 'Context size (estimated)', unit: 'tokens', estimated: true, empty_hint: null, kind: 'scalar', parts: [] },
    {
      id: 'context_composition', label: 'Context composition (estimated)', unit: 'tokens', estimated: true, empty_hint: null,
      kind: 'composition',
      parts: [
        { id: 'system_prompt', label: 'System Prompt' },
        { id: 'tool_results', label: 'Tool Results' },
        { id: 'conversation_history', label: 'Conversation History' },
      ],
    },
    { id: 'block_count', label: 'Blocks (input)', unit: 'count', estimated: false, empty_hint: 'No block data for these requests', kind: 'scalar', parts: [] },
    { id: 'cache_hit_pct', label: 'Cache hit %', unit: 'percent', estimated: false, empty_hint: 'Provider did not report cache usage', kind: 'scalar', parts: [] },
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
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      'Context size (estimated)', 'Context composition (estimated)', 'Blocks (input)', 'Cache hit %',
    ])
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

  describe('legend of many conversations', () => {
    const many: SessionTrend = {
      ...data,
      series: Array.from({ length: 7 }, (_, i) => ({
        key: `c${i + 1}`, label: `Conversation ${i + 1}`, auxiliary: false, request_count: 1, points: [point(i + 1, {})],
      })),
    }

    it('lists the first four and offers Show all', () => {
      show({ data: many })
      expect(screen.getAllByRole('checkbox')).toHaveLength(4)
      expect(screen.queryByRole('checkbox', { name: /Conversation 5/ })).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Show all (7)' }))
      expect(screen.getAllByRole('checkbox')).toHaveLength(7)
      fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }))
      expect(screen.getAllByRole('checkbox')).toHaveLength(4)
    })

    it('keeps a drawn line in the legend after collapsing', () => {
      const { container } = show({ data: many })
      fireEvent.click(screen.getByRole('button', { name: 'Show all (7)' }))
      fireEvent.click(screen.getByRole('checkbox', { name: /Conversation 6/ }))
      fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }))
      expect(screen.getAllByRole('checkbox')).toHaveLength(5)
      expect((screen.getByRole('checkbox', { name: /Conversation 6/ }) as HTMLInputElement).checked).toBe(true)
      expect(container.querySelectorAll('.recharts-line')).toHaveLength(5)
    })

    it('has no toggle when there are four or fewer', () => {
      show()
      expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
    })
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

  describe('composition metrics', () => {
    const twoConversations: SessionTrend = {
      ...data,
      series: [
        { key: 'small', label: 'Conversation 1', auxiliary: false, request_count: 1, points: [point(1, {})] },
        { key: 'big', label: 'Conversation 2', auxiliary: false, request_count: 3, points: [point(2, {}), point(3, {}), point(4, {})] },
        { key: 'aux', label: 'Auxiliary requests', auxiliary: true, request_count: 9, points: [point(5, {})] },
      ],
    }

    it('stacks one conversation: the largest non-auxiliary by default, switchable', () => {
      const { container } = show({ data: twoConversations, metricId: 'context_composition' })
      const select = screen.getByRole('combobox', { name: 'Conversation' }) as HTMLSelectElement
      expect(select.value).toBe('big')
      expect(container.querySelectorAll('.recharts-area')).toHaveLength(3)
      expect(screen.queryByRole('checkbox')).toBeNull()
      fireEvent.change(select, { target: { value: 'small' } })
      expect(select.value).toBe('small')
    })

    it('shows the parts legend and switches between tokens and share', () => {
      show({ data: twoConversations, metricId: 'context_composition' })
      expect(screen.getByText('System Prompt')).toBeTruthy()
      expect(screen.getByText('Conversation History')).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Tokens' }).getAttribute('aria-pressed')).toBe('true')
      fireEvent.click(screen.getByRole('button', { name: '% of total' }))
      expect(screen.getByRole('button', { name: '% of total' }).getAttribute('aria-pressed')).toBe('true')
      expect(screen.getByRole('button', { name: 'Tokens' }).getAttribute('aria-pressed')).toBe('false')
    })

    it('hides the conversation selector for a single series and the line legend never shows', () => {
      show({ data: { ...data, series: [data.series[0]] }, metricId: 'context_composition' })
      expect(screen.queryByRole('combobox', { name: 'Conversation' })).toBeNull()
      expect(screen.getByRole('combobox', { name: 'Trend metric' })).toBeTruthy()
    })

    it('shows the empty hint when every part is zero', () => {
      const zero: SessionTrend = {
        ...data,
        series: [{ ...data.series[0], points: data.series[0].points.map((p) => ({ ...p, parts: { context_composition: { system_prompt: 0 } } })) }],
      }
      show({ data: zero, metricId: 'context_composition' })
      expect(screen.getByText('No data for this metric')).toBeTruthy()
    })

    it('opens the request under the pointer when the stacked chart is clicked', () => {
      const onPointClick = vi.fn()
      const { container } = show({ data: twoConversations, metricId: 'context_composition', onPointClick })
      const wrapper = container.querySelector('.recharts-wrapper') as HTMLElement
      fireEvent.mouseMove(wrapper, { clientX: 590, clientY: 100 })
      fireEvent.click(wrapper, { clientX: 590, clientY: 100 })
      expect(onPointClick).toHaveBeenCalledWith('r4')
    })
  })

  it('plots the block count as a line', () => {
    const { container } = show({ metricId: 'block_count', data: { ...data, series: data.series.map((s) => ({ ...s, points: s.points.map((p, i) => ({ ...p, values: { ...p.values, block_count: 10 * (i + 1) } })) })) } })
    expect(container.querySelectorAll('.recharts-line')).toHaveLength(1)
    expect(screen.getByRole('checkbox', { name: /Conversation 1/ })).toBeTruthy()
  })
})
