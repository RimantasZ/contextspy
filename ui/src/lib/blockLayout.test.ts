import { describe, expect, it } from 'vitest'
import { layoutProportionalBlocks, proportionalBlockSpan } from './blockLayout'
import { makeBlock } from '../test/fixtures'

describe('proportionalBlockSpan', () => {
  it('gives zero-token and small blocks the same minimum footprint as compact blocks', () => {
    expect(proportionalBlockSpan(0)).toBe(1)
    expect(proportionalBlockSpan(1)).toBe(1)
    expect(proportionalBlockSpan(64)).toBe(1)
  })

  it('grows discretely with token count and caps very large blocks', () => {
    expect([64, 65, 129, 257, 513, 1025].map((tokens) => proportionalBlockSpan(tokens))).toEqual([1, 2, 3, 4, 5, 6])
    expect(proportionalBlockSpan(1_000_000)).toBe(6)
  })
})

describe('layoutProportionalBlocks', () => {
  it('preserves caller-provided order and renders exactly one item per block', () => {
    const blocks = [
      makeBlock({ id: 3, position: 2, token_count: 2_000 }),
      makeBlock({ id: 1, position: 0, token_count: 20 }),
      makeBlock({ id: 2, position: 1, token_count: 200 }),
    ]
    const layout = layoutProportionalBlocks(blocks, 12)
    expect(layout.rows.flatMap((row) => row.items).map((item) => item.blockId)).toEqual([3, 1, 2])
    expect(layout.rows.flatMap((row) => row.items)).toHaveLength(blocks.length)
    expect(layout.totalTokens).toBe(2_220)
  })

  it('moves a block intact to the next row and leaves the prior remainder empty', () => {
    const layout = layoutProportionalBlocks([
      makeBlock({ id: 1, position: 0, token_count: 1_100 }), // span 6
      makeBlock({ id: 2, position: 1, token_count: 260 }), // span 4
      makeBlock({ id: 3, position: 2, token_count: 130 }), // span 3; cannot fit in row 1
    ], 12)
    expect(layout.rows).toHaveLength(2)
    expect(layout.rows[0]).toMatchObject({ usedColumns: 10, capacity: 12 })
    expect(layout.rows[0].items.map((item) => item.blockId)).toEqual([1, 2])
    expect(layout.rows[1].items[0]).toMatchObject({ blockId: 3, columnStart: 0, span: 3 })
  })

  it('keeps zero-token blocks in sequence at the minimum size', () => {
    const layout = layoutProportionalBlocks([
      makeBlock({ id: 1, position: 0, token_count: 100 }),
      makeBlock({ id: 9, position: 1, token_count: 0 }),
    ], 8)
    expect(layout.rows.flatMap((row) => row.items).map((item) => [item.blockId, item.span])).toEqual([[1, 2], [9, 1]])
    expect(layout.totalTokens).toBe(100)
  })

  it('handles empty input', () => {
    expect(layoutProportionalBlocks([], 10)).toEqual({ rows: [], totalTokens: 0, capacity: 10 })
  })
})
