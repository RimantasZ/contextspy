import type { KeyboardEvent } from 'react'
import type { RequestBlock } from '../../api/client'
import type { BlockGrouping } from '../../lib/blockArrangement'
import { blockGroupKey } from '../../lib/blockArrangement'
import { BlockTile } from './BlockTile'

export function CompactBlockMap({ blocks, selectedId, density, grouping, onSelect }: {
  blocks: RequestBlock[]
  selectedId: number | null
  density: number
  grouping: BlockGrouping
  onSelect: (block: RequestBlock | null) => void
}) {
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onSelect(null)
      return
    }
    if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const root = event.currentTarget.closest('[data-block-map]')
    const buttons = [...(root?.querySelectorAll<HTMLButtonElement>('[data-block-map-item]') ?? [])]
    let next = index
    if (event.key === 'ArrowRight') next += 1
    if (event.key === 'ArrowLeft') next -= 1
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = buttons.length - 1
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const currentTop = buttons[index]?.offsetTop ?? 0
      const rows = [...new Set(buttons.map((button) => button.offsetTop))]
      const currentRow = rows.indexOf(currentTop)
      const targetTop = rows[currentRow + (event.key === 'ArrowDown' ? 1 : -1)]
      if (targetTop != null) {
        const sameColumn = buttons.filter((button) => button.offsetTop === targetTop)
        const currentLeft = buttons[index]?.offsetLeft ?? 0
        const target = sameColumn.reduce((best, button) => Math.abs(button.offsetLeft - currentLeft) < Math.abs(best.offsetLeft - currentLeft) ? button : best)
        next = buttons.indexOf(target)
      }
    }
    const target = buttons[Math.max(0, Math.min(buttons.length - 1, next))]
    target?.focus()
    const targetBlock = blocks.find((block) => block.id === Number(target?.dataset.blockId))
    if (targetBlock) onSelect(targetBlock)
  }

  if (blocks.length === 0) return <div className="p-8 text-center text-sm text-[var(--text-muted)]">No blocks match these filters.</div>

  return (
    <div
      data-block-map
      role="group"
      aria-label="Compact block map"
      className="grid content-start gap-1 p-3"
      style={{ gridTemplateColumns: `repeat(auto-fill, ${density}px)`, gridAutoRows: `${density}px` }}
    >
      {blocks.map((block, index) => {
        const selected = selectedId === block.id
        const startsGroup = index > 0 && blockGroupKey(blocks[index - 1], grouping) !== blockGroupKey(block, grouping)
        return (
          <BlockTile
            key={block.id}
            id={`block-tile-${block.id}`}
            block={block}
            selected={selected}
            tabIndex={selected || (selectedId == null && index === 0) ? 0 : -1}
            onSelect={() => onSelect(selected ? null : block)}
            onKeyDown={(event) => onKeyDown(event, index)}
            style={{ boxShadow: startsGroup ? '-3px 0 0 var(--focus)' : undefined }}
          />
        )
      })}
    </div>
  )
}
