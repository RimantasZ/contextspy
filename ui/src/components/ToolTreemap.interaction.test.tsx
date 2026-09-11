import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TOOL_TREEMAP_FRAME_STYLE, ToolTreemapSelectionDetails, TreemapContent } from './ToolTreemap'

describe('tool treemap interaction', () => {
  it('uses the same one-pixel graphical border for the frame and block separators', () => {
    expect(TOOL_TREEMAP_FRAME_STYLE.border).toBe('1px solid var(--graphical-border)')
  })

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
    expect(rect?.hasAttribute('opacity')).toBe(false)
    expect(container.querySelector('path')).toBeNull()

    await userEvent.click(block)
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ key: 'search:result', value: 80 }))

    block.focus()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('keeps every block fill unchanged and marks selection with an inset focus outline', () => {
    const { container } = render(
      <svg>
        <TreemapContent
          x={12}
          y={8}
          width={120}
          height={60}
          depth={2}
          name="Results"
          value={80}
          fill="#abc"
          toolName="search"
          category="result"
          selectedKey="search:result"
        />
      </svg>,
    )

    const rectangles = container.querySelectorAll('rect')
    expect(rectangles).toHaveLength(2)
    expect(rectangles[0].getAttribute('fill')).toBe('#abc')
    expect(rectangles[0].hasAttribute('opacity')).toBe(false)

    const outline = container.querySelector('[data-treemap-selection-outline]')
    expect(outline?.getAttribute('x')).toBe('13')
    expect(outline?.getAttribute('y')).toBe('9')
    expect(outline?.getAttribute('width')).toBe('118')
    expect(outline?.getAttribute('height')).toBe('58')
    expect(outline?.getAttribute('fill')).toBe('none')
    expect(outline?.getAttribute('stroke')).toBe('var(--focus)')
    expect(outline?.getAttribute('stroke-width')).toBe('2')
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
