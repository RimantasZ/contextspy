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
import { cloneElement } from 'react'
import type { ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { DashboardActivityPoint } from '../../api/client'
import { formatCompactTokens, formatTokenTooltip, toActivityChartData } from './dashboardFormat'
import { RequestActivityChart } from './RequestActivityChart'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children, { width: 600, height: 220 } as object),
  }
})

const activity: DashboardActivityPoint[] = [
  { id: 'r3', session_seq: 3, timestamp: '2026-09-18T08:03:00', tokens_total_input: 120000, tokens_total_output: 340 },
  { id: 'r4', session_seq: 4, timestamp: '2026-09-18T08:04:00', tokens_total_input: 87412, tokens_total_output: 612 },
]

describe('activity chart helpers', () => {
  it('keeps chronological order and raw values', () => {
    expect(toActivityChartData(activity)).toEqual([
      { label: '#3', tokens_total_input: 120000, tokens_total_output: 340 },
      { label: '#4', tokens_total_input: 87412, tokens_total_output: 612 },
    ])
  })

  it('uses a short id label when the sequence is null', () => {
    expect(toActivityChartData([{ ...activity[0], id: 'abcdef1234', session_seq: null }])[0].label).toBe('abcdef12')
  })

  it('formats ticks compactly and tooltips exactly', () => {
    expect([950, 1200, 87412, 120000, 2_500_000].map(formatCompactTokens)).toEqual(['950', '1.2k', '87k', '120k', '2.5M'])
    expect(formatTokenTooltip(87412, 'Input')).toEqual(['87,412 tokens', 'Input'])
  })
})

describe('RequestActivityChart', () => {
  it('renders two Y axes and two bar series', () => {
    const { container } = render(<RequestActivityChart activity={activity} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/left axis.*right axis/)
    expect(container.querySelectorAll('.recharts-yAxis').length).toBe(2)
    expect(container.querySelectorAll('.recharts-bar').length).toBe(2)
    expect(screen.getByText('Input (left axis)')).toBeTruthy()
    expect(screen.getByText('Output (right axis)')).toBeTruthy()
  })

  it('shows an empty state without data', () => {
    render(<RequestActivityChart activity={[]} />)
    expect(screen.getByText(/No requests captured/)).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
