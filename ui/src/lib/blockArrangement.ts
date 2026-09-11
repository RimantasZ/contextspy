import type { RequestBlock } from '../api/client'
import type { BlockVisual } from './blockVisuals'
import { sortedBlocks, visualOf } from './blockVisuals'

export type BlockGrouping = 'sequence' | 'turn' | 'toolPair'
type BlockSorting = 'requestOrder' | 'largestFirst'
export type ArrangementPreset = 'sequence' | 'turn' | 'toolPair' | 'largestFirst'

export interface BlockArrangement {
  grouping: BlockGrouping
  sorting: BlockSorting
}

export const ARRANGEMENT_PRESETS: Record<ArrangementPreset, BlockArrangement> = {
  sequence: { grouping: 'sequence', sorting: 'requestOrder' },
  turn: { grouping: 'turn', sorting: 'requestOrder' },
  toolPair: { grouping: 'toolPair', sorting: 'requestOrder' },
  largestFirst: { grouping: 'sequence', sorting: 'largestFirst' },
}

export function arrangeBlocks(blocks: RequestBlock[], arrangement: BlockArrangement): RequestBlock[] {
  const ordered = sortedBlocks(blocks)
  if (arrangement.sorting === 'requestOrder') return ordered
  return ordered.sort((left, right) => right.token_count - left.token_count || left.position - right.position || left.id - right.id)
}

export function blockGroupKey(block: RequestBlock, grouping: BlockGrouping): string {
  if (grouping === 'turn') return block.message_index == null ? 'structural' : `message-${block.message_index}`
  if (grouping === 'toolPair') return block.tool_call_id ?? block.tool_name ?? `block-${block.id}`
  return 'sequence'
}

export interface WorkbenchBlockModel {
  allBlocks: RequestBlock[]
  directionBlocks: RequestBlock[]
  visibleBlocks: RequestBlock[]
  available: Set<BlockVisual>
  tokenTotals: Partial<Record<BlockVisual, number>>
}

export function buildWorkbenchBlockModel(blocks: RequestBlock[], options: {
  direction: 'input' | 'output'
  activeTypes: ReadonlySet<BlockVisual>
  hideZero: boolean
  query: string
  arrangement: BlockArrangement
}): WorkbenchBlockModel {
  const allBlocks = sortedBlocks(blocks)
  const directionBlocks = allBlocks.filter((block) => block.direction === options.direction)
  const available = new Set(directionBlocks.map(visualOf))
  const tokenTotals = directionBlocks.reduce<Partial<Record<BlockVisual, number>>>((totals, block) => {
    const visual = visualOf(block)
    totals[visual] = (totals[visual] ?? 0) + block.token_count
    return totals
  }, {})
  const needle = options.query.trim().toLocaleLowerCase()
  const filtered = directionBlocks.filter((block) => {
    if (options.hideZero && block.token_count <= 0) return false
    if (!options.activeTypes.has(visualOf(block))) return false
    if (!needle) return true
    return [block.content, block.tool_name, block.block_type, block.category, block.tool_call_id]
      .some((value) => String(value ?? '').toLocaleLowerCase().includes(needle))
  })
  return { allBlocks, directionBlocks, visibleBlocks: arrangeBlocks(filtered, options.arrangement), available, tokenTotals }
}
