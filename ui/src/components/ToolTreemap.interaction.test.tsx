import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ToolTreemapSelectionDetails, TreemapContent } from './ToolTreemap'

describe('tool treemap interaction', () => {
  it('only renders compact details after a block is selected', () => {
    const { container, rerender } = render(<ToolTreemapSelectionDetails selected={null} />)
    expect(container.childElementCount).toBe(0)

    rerender(<ToolTreemapSelectionDetails selected={{ key: 'search:result', label: 'search · Results', value: 94104 }} />)
    expect(screen.getByRole('status').textContent).toBe('search · Results94,104 tokens')
    expect(screen.queryByText(/Select a rectangle/i)).toBeNull()
  })

  it('does not draw parent rectangles that could duplicate leaf borders', () => {
    const { container } = render(
      <svg>
        <TreemapContent depth={1} x={0} y={0} width={120} height={60} fill="#aaa" />
      </svg>,
    )

    expect(container.querySelector('rect')).toBeNull()
  })

  it('selects a leaf with pointer or keyboard and draws only its leading separators', async () => {
    const onSelect = vi.fn()
    const { container } = render(
      <svg>
        <TreemapContent
          x={0}
          y={0}
          width={120}
          height={60}
          depth={2}
          name="Results"
          value={80}
          fill="#aaa"
          toolName="search"
          category="result"
          onSelect={onSelect}
        />
      </svg>,
    )

    const block = screen.getByRole('button', { name: 'search · Results, 80 tokens' })
    const rect = container.querySelector('rect')
    expect(rect?.getAttribute('shape-rendering')).toBe('crispEdges')
    expect(rect?.hasAttribute('stroke')).toBe(false)
    expect(container.querySelector('path')).toBeNull()

    await userEvent.click(block)
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ key: 'search:result', value: 80 }))

    block.focus()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('draws a shared internal edge once from the block on its right', () => {
    const { container } = render(
      <svg>
        <TreemapContent depth={2} x={0} y={0} width={50} height={60} name="Results" toolName="left" />
        <TreemapContent depth={2} x={50} y={0} width={70} height={60} name="Results" toolName="right" />
      </svg>,
    )

    const paths = container.querySelectorAll('path')
    expect(paths).toHaveLength(1)
    expect(paths[0].getAttribute('d')).toBe('M 50 0 V 60')
    expect(paths[0].getAttribute('stroke-width')).toBe('1')
  })
})
