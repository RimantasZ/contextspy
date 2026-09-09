import type { KeyboardEvent } from 'react'
import type { RequestBlock } from '../../api/client'
import type { GroupMode } from './BlockToolbar'
import { BLOCK_VISUALS, blockAccessibleName, visualOf } from '../../lib/blockVisuals'

function groupKey(block: RequestBlock, grouping: GroupMode): string {
  if (grouping === 'turn') return block.message_index == null ? 'structural' : `message-${block.message_index}`
  if (grouping === 'tool') return block.tool_call_id ?? block.tool_name ?? `block-${block.id}`
  return 'sequence'
}

export function CompactBlockMap({ blocks, selectedId, density, grouping, onSelect }: {
  blocks: RequestBlock[]
  selectedId: number | null
  density: number
  grouping: GroupMode
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
        const visual = visualOf(block)
        const selected = selectedId === block.id
        const startsGroup = index > 0 && groupKey(blocks[index - 1], grouping) !== groupKey(block, grouping)
        return (
          <button
            key={block.id}
            id={`block-tile-${block.id}`}
            data-block-map-item
            data-block-id={block.id}
            type="button"
            aria-label={blockAccessibleName(block)}
            aria-pressed={selected}
            tabIndex={selected || (selectedId == null && index === 0) ? 0 : -1}
            title={blockAccessibleName(block)}
            onClick={() => onSelect(selected ? null : block)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className="relative rounded-[3px] border border-[var(--graphical-border)] text-[9px] font-bold leading-none text-[var(--block-ink)] transition-transform hover:z-[1] hover:scale-110"
            style={{
              background: BLOCK_VISUALS[visual].color,
              borderColor: BLOCK_VISUALS[visual].border,
              boxShadow: selected ? '0 0 0 3px var(--focus)' : startsGroup ? '-3px 0 0 var(--focus)' : undefined,
            }}
          >
            <span aria-hidden="true">{BLOCK_VISUALS[visual].short}</span>
            {block.content_purged && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-[var(--surface)] bg-[var(--danger)]" />}
          </button>
        )
      })}
    </div>
  )
}
