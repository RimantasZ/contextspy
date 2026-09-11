import type { CSSProperties, KeyboardEvent, ReactNode } from 'react'
import type { RequestBlock } from '../../api/client'
import { BLOCK_VISUALS, blockAccessibleName, blockBackground, visualOf } from '../../lib/blockVisuals'

export function BlockTile({ block, id, selected, tabIndex, className = '', style, children, onSelect, onKeyDown }: {
  block: RequestBlock
  id: string
  selected: boolean
  tabIndex: number
  className?: string
  style?: CSSProperties
  children?: ReactNode
  onSelect: () => void
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
}) {
  const visual = BLOCK_VISUALS[visualOf(block)]
  return (
    <button
      id={id} data-block-map-item data-block-id={block.id} type="button"
      aria-label={blockAccessibleName(block)} aria-pressed={selected} tabIndex={tabIndex}
      title={blockAccessibleName(block)} onClick={onSelect} onKeyDown={onKeyDown}
      className={`composition-block border-[var(--graphical-border)] ${className}`}
      style={{ backgroundColor: blockBackground(block), borderColor: visual.border, ...style, boxShadow: selected ? '0 0 0 3px var(--focus)' : style?.boxShadow }}
    >
      {children ?? <span aria-hidden="true">{visual.short}</span>}
      {block.content_purged && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-[var(--surface)] bg-[var(--danger)]" />}
    </button>
  )
}
