import type { RequestBlock } from '../api/client'
import { sortedBlocks } from './blockVisuals'

export interface BlockSegment {
  block: RequestBlock
  blockId: number
  rowIndex: number
  segmentIndex: number
  tokenOffset: number
  tokens: number
  leftPct: number
  widthPct: number
  startsBlock: boolean
  endsBlock: boolean
}

export interface ProportionalRow {
  index: number
  usedTokens: number
  capacity: number
  segments: BlockSegment[]
}

export interface ProportionalLayout {
  capacity: number
  totalTokens: number
  rows: ProportionalRow[]
  zeroTokenBlocks: RequestBlock[]
}

export function suggestedRowCapacity(blocks: RequestBlock[], targetRows = 6, minimum = 200): number {
  const total = blocks.reduce((sum, block) => sum + Math.max(0, block.token_count), 0)
  return Math.max(minimum, Math.ceil(total / Math.max(1, targetRows)))
}

/**
 * Lays ordered blocks onto fixed-capacity rows. Segment width is always the
 * segment's token count divided by capacity; blocks that cross a row boundary
 * are split without changing source order or token totals.
 */
export function layoutProportionalBlocks(blocks: RequestBlock[], requestedCapacity?: number): ProportionalLayout {
  const ordered = sortedBlocks(blocks)
  const zeroTokenBlocks = ordered.filter((block) => block.token_count <= 0)
  const positive = ordered.filter((block) => block.token_count > 0)
  const capacity = Math.max(1, Math.floor(requestedCapacity ?? suggestedRowCapacity(positive)))
  const rows: ProportionalRow[] = []
  let row: ProportionalRow | null = null

  const ensureRow = () => {
    if (!row || row.usedTokens >= capacity) {
      row = { index: rows.length, usedTokens: 0, capacity, segments: [] }
      rows.push(row)
    }
    return row
  }

  for (const block of positive) {
    let remaining = block.token_count
    let tokenOffset = 0
    let segmentIndex = 0

    while (remaining > 0) {
      const current = ensureRow()
      const available = capacity - current.usedTokens
      const tokens = Math.min(remaining, available)
      current.segments.push({
        block,
        blockId: block.id,
        rowIndex: current.index,
        segmentIndex,
        tokenOffset,
        tokens,
        leftPct: (current.usedTokens / capacity) * 100,
        widthPct: (tokens / capacity) * 100,
        startsBlock: tokenOffset === 0,
        endsBlock: remaining === tokens,
      })
      current.usedTokens += tokens
      tokenOffset += tokens
      remaining -= tokens
      segmentIndex += 1
    }
  }

  return {
    capacity,
    totalTokens: positive.reduce((sum, block) => sum + block.token_count, 0),
    rows,
    zeroTokenBlocks,
  }
}
