import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

vi.mock('recharts', async () => {
  const React = await import('react')
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    Treemap: ({ data, content, children }: {
      data: Array<{ children: Array<{ name: string; toolName: string; category: string; value: number; fill: string }> }>
      content: React.ReactElement
      children: React.ReactNode
    }) => React.createElement('svg', null,
      data.flatMap((node) => node.children).map((leaf, index) => React.cloneElement(content, {
        ...leaf, key: `${leaf.toolName}-${leaf.category}`, depth: 2, x: index * 100, y: 0, width: 100, height: 60,
      })),
      children,
    ),
    Tooltip: ({ content }: { content: React.ReactElement }) => React.cloneElement(content, {
      active: true,
      payload: [{ payload: { toolName: 'exec', name: 'Definitions' } }],
    }),
  }
})

import { ToolTreemap } from './ToolTreemap'

const tools = [{ tool_name: 'exec', definition_tokens: 20, result_tokens: 80 }]

describe('tool treemap interaction', () => {
  it('uses a one-pixel frame and keeps hover details compact', () => {
    const { container } = render(<ToolTreemap tools={tools} totalInputTokens={200} />)
    expect(container.querySelector('[data-tool-treemap-frame]')?.getAttribute('style')).toContain('1px solid var(--graphical-border)')
    expect(screen.getByText('exec definitions')).toBeTruthy()
    expect(screen.getByText('exec definitions').parentElement?.textContent).not.toMatch(/tokens|%/)
  })

  it('selects a block through the public chart and then shows detailed status', async () => {
    const { container } = render(<ToolTreemap tools={tools} totalInputTokens={200} />)
    expect(screen.queryByText('80 tokens')).toBeNull()
    const result = screen.getByRole('button', { name: 'exec results, 80 tokens' })
    await userEvent.click(result)
    expect(screen.getByText('80 tokens')).toBeTruthy()
    expect(screen.getByText('80.0% of tool footprint')).toBeTruthy()
    expect(screen.getByText('40.0% of context window')).toBeTruthy()
    expect(container.querySelector('[data-treemap-selection-outline]')).toBeTruthy()
    result.focus()
    await userEvent.keyboard('{Enter}')
    expect(result.getAttribute('aria-pressed')).toBe('true')
  })
})
