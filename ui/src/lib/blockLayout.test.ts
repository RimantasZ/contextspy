import { describe, expect, it } from 'vitest'
import { layoutProportionalBlocks } from './blockLayout'
import { makeBlock } from '../test/fixtures'

describe('layoutProportionalBlocks', () => {
  it('preserves position order and conserves every token', () => {
    const blocks = [
      makeBlock({ id: 3, position: 2, token_count: 55 }),
      makeBlock({ id: 1, position: 0, token_count: 25 }),
      makeBlock({ id: 2, position: 1, token_count: 40 }),
    ]
    const layout = layoutProportionalBlocks(blocks, 50)
    expect(layout.rows.flatMap((row) => row.segments).map((segment) => segment.blockId).filter((id, index, values) => index === 0 || values[index - 1] !== id)).toEqual([1, 2, 3])
    expect(layout.rows.flatMap((row) => row.segments).reduce((sum, segment) => sum + segment.tokens, 0)).toBe(120)
    expect(layout.totalTokens).toBe(120)
  })

  it('fills fixed-capacity rows and splits oversized blocks', () => {
    const layout = layoutProportionalBlocks([makeBlock({ id: 1, token_count: 125 })], 50)
    expect(layout.rows.map((row) => row.usedTokens)).toEqual([50, 50, 25])
    expect(layout.rows.map((row) => row.segments[0].widthPct)).toEqual([100, 100, 50])
    expect(layout.rows.flatMap((row) => row.segments).map((segment) => [segment.startsBlock, segment.endsBlock])).toEqual([[true, false], [false, false], [false, true]])
  })

  it('keeps zero-token blocks outside the proportional calculation', () => {
    const zero = makeBlock({ id: 9, token_count: 0 })
    const layout = layoutProportionalBlocks([zero, makeBlock({ id: 2, token_count: 10 })], 20)
    expect(layout.zeroTokenBlocks).toEqual([zero])
    expect(layout.totalTokens).toBe(10)
    expect(layout.rows[0].usedTokens).toBe(10)
  })

  it('handles empty input', () => {
    expect(layoutProportionalBlocks([], 100)).toMatchObject({ rows: [], zeroTokenBlocks: [], totalTokens: 0, capacity: 100 })
  })
})
