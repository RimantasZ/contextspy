import type { RequestBlock } from '../api/client'
import { sortedBlocks } from './blockVisuals'

export interface ProportionalBlockItem {
  block: RequestBlock
  blockId: number
  columnStart: number
  span: number
}

export interface ProportionalRow {
  index: number
  usedColumns: number
  capacity: number
  tokens: number
  items: ProportionalBlockItem[]
}

export interface ProportionalLayout {
  capacity: number
  totalTokens: number
  rows: ProportionalRow[]
}

const TOKENS_PER_FIRST_GROWTH_STEP = 64
export const MAX_PROPORTIONAL_SPAN = 6

/**
 * Maps token count to a compact-grid span. Every block gets at least one tile;
 * logarithmic growth keeps large payloads visually important without allowing
 * a single block to dominate the canvas.
 */
export function proportionalBlockSpan(tokenCount: number, maximum = MAX_PROPORTIONAL_SPAN): number {
  const max = Math.max(1, Math.floor(maximum))
  if (tokenCount <= TOKENS_PER_FIRST_GROWTH_STEP) return 1
  return Math.min(max, 1 + Math.ceil(Math.log2(tokenCount / TOKENS_PER_FIRST_GROWTH_STEP)))
}

/**
 * Places whole blocks into fixed-column rows. A block that does not fit moves
 * intact to the next row, deliberately preserving the unused cells at the end
 * of the previous row. Blocks are never split across rows.
 */
export function layoutProportionalBlocks(blocks: RequestBlock[], requestedCapacity = 12): ProportionalLayout {
  const ordered = sortedBlocks(blocks)
  const capacity = Math.max(1, Math.floor(requestedCapacity))
  const rows: ProportionalRow[] = []

  for (const block of ordered) {
    const span = Math.min(capacity, proportionalBlockSpan(block.token_count))
    let row = rows[rows.length - 1]
    if (!row || row.usedColumns + span > capacity) {
      row = { index: rows.length, usedColumns: 0, capacity, tokens: 0, items: [] }
      rows.push(row)
    }
    row.items.push({ block, blockId: block.id, columnStart: row.usedColumns, span })
    row.usedColumns += span
    row.tokens += Math.max(0, block.token_count)
  }

  return {
    capacity,
    totalTokens: ordered.reduce((sum, block) => sum + Math.max(0, block.token_count), 0),
    rows,
  }
}
