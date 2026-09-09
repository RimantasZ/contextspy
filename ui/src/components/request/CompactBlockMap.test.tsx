import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeBlock } from '../../test/fixtures'
import { CompactBlockMap } from './CompactBlockMap'

describe('CompactBlockMap', () => {
  const blocks = [makeBlock({ id: 1, position: 0, block_type: 'system_prompt' }), makeBlock({ id: 2, position: 1, content: 'second' })]

  it('renders one named tile per visible block and selects it', async () => {
    const onSelect = vi.fn()
    render(<CompactBlockMap blocks={blocks} selectedId={null} density={26} grouping="sequence" onSelect={onSelect} />)
    expect(screen.getAllByRole('button')).toHaveLength(2)
    const second = screen.getByRole('button', { name: /User.*position 2/i })
    await userEvent.click(second)
    expect(onSelect).toHaveBeenCalledWith(blocks[1])
  })

  it('supports arrow navigation and Escape', () => {
    const onSelect = vi.fn()
    render(<CompactBlockMap blocks={blocks} selectedId={null} density={26} grouping="turn" onSelect={onSelect} />)
    const first = screen.getByRole('button', { name: /System/i })
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenLastCalledWith(blocks[1])
    fireEvent.keyDown(screen.getByRole('button', { name: /User/i }), { key: 'Escape' })
    expect(onSelect).toHaveBeenLastCalledWith(null)
  })

  it('marks zero-token blocks without replacing their category color', () => {
    const zero = makeBlock({ id: 3, token_count: 0 })
    render(<CompactBlockMap blocks={[zero]} selectedId={null} density={26} grouping="sequence" onSelect={vi.fn()} />)
    const tile = screen.getByRole('button', { name: /0 tokens/i })
    expect(tile.className).toContain('composition-block-zero')
    expect(tile.style.backgroundColor).toBe('var(--block-user)')
  })
})
