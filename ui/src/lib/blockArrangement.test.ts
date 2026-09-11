import { describe, expect, it } from 'vitest'
import { makeBlock } from '../test/fixtures'
import { ARRANGEMENT_PRESETS, arrangeBlocks, blockGroupKey, buildWorkbenchBlockModel } from './blockArrangement'

describe('block arrangement', () => {
  const blocks = [
    makeBlock({ id: 3, position: 2, token_count: 20, content: 'third' }),
    makeBlock({ id: 1, position: 0, token_count: 20, content: 'first' }),
    makeBlock({ id: 2, position: 1, token_count: 200, content: 'second' }),
  ]

  it('preserves request order and uses position as a stable size tie-breaker', () => {
    expect(arrangeBlocks(blocks, ARRANGEMENT_PRESETS.sequence).map((block) => block.id)).toEqual([1, 2, 3])
    expect(arrangeBlocks(blocks, ARRANGEMENT_PRESETS.largestFirst).map((block) => block.id)).toEqual([2, 1, 3])
  })

  it('provides stable group keys', () => {
    const block = makeBlock({ id: 7, message_index: 2, tool_call_id: 'call-1' })
    expect(blockGroupKey(block, 'sequence')).toBe('sequence')
    expect(blockGroupKey(block, 'turn')).toBe('message-2')
    expect(blockGroupKey(block, 'toolPair')).toBe('call-1')
  })

  it('derives filters, totals, and visible blocks in one model', () => {
    const model = buildWorkbenchBlockModel([
      ...blocks,
      makeBlock({ id: 4, direction: 'output', token_count: 5 }),
      makeBlock({ id: 5, position: 3, token_count: 0 }),
    ], { direction: 'input', activeTypes: new Set(['user']), hideZero: true, query: 'second', arrangement: ARRANGEMENT_PRESETS.sequence })
    expect(model.directionBlocks).toHaveLength(4)
    expect(model.visibleBlocks.map((block) => block.id)).toEqual([2])
    expect(model.tokenTotals.user).toBe(240)
  })
})
