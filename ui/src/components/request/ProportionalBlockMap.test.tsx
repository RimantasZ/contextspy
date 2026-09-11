import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { makeBlock } from '../../test/fixtures'
import { ProportionalBlockMap } from './ProportionalBlockMap'

describe('ProportionalBlockMap', () => {
  const blocks = [
    makeBlock({ id: 1, position: 0, block_type: 'system_prompt', token_count: 20 }),
    makeBlock({ id: 2, position: 1, token_count: 200 }),
    makeBlock({ id: 3, position: 2, token_count: 5_000 }),
  ]

  it('renders one discrete tile per block with minimum and capped spans', () => {
    render(<ProportionalBlockMap blocks={blocks} selectedId={null} density={26} onSelect={vi.fn()} />)
    const tiles = screen.getAllByRole('button')
    expect(tiles).toHaveLength(3)
    expect(tiles.map((tile) => tile.style.gridColumn)).toEqual(['1 / span 1', '2 / span 3', '5 / span 6'])
  })

  it('retains keyboard selection and Escape behavior', () => {
    const onSelect = vi.fn()
    render(<ProportionalBlockMap blocks={blocks} selectedId={null} density={26} onSelect={onSelect} />)
    const first = screen.getByRole('button', { name: /System/i })
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenLastCalledWith(blocks[1])
    fireEvent.keyDown(screen.getByRole('button', { name: /200 tokens/i }), { key: 'Escape' })
    expect(onSelect).toHaveBeenLastCalledWith(null)
  })

  it('mutes zero-token blocks in proportional view', () => {
    const zero = makeBlock({ id: 4, token_count: 0 })
    render(<ProportionalBlockMap blocks={[zero]} selectedId={null} density={26} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /0 tokens/i }).style.backgroundColor).toContain('var(--zero-token-neutral)')
  })
})
