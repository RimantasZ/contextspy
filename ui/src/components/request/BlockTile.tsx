import type { CSSProperties, KeyboardEvent, ReactNode } from 'react'
import type { RequestBlock } from '../../api/client'
import { BLOCK_VISUALS, blockAccessibleName, blockBackground, visualOf } from '../../lib/blockVisuals'

export function BlockTile({ block, id, selected, dimmed = false, tabIndex, className = '', style, children, onSelect, onKeyDown }: {
  block: RequestBlock
  id: string
  selected: boolean
  /** Already present in the previous request (drawn at 50% opacity unless selected). */
  dimmed?: boolean
  tabIndex: number
  className?: string
  style?: CSSProperties
  children?: ReactNode
  onSelect: () => void
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
}) {
  const visual = BLOCK_VISUALS[visualOf(block)]
  const label = dimmed ? `${blockAccessibleName(block)} (already in previous request)` : blockAccessibleName(block)
  return (
    <button
      id={id} data-block-map-item data-block-id={block.id} type="button"
      aria-label={label} aria-pressed={selected} tabIndex={tabIndex}
      title={label} onClick={onSelect} onKeyDown={onKeyDown}
      className={`composition-block border-[var(--graphical-border)] ${dimmed && !selected ? 'opacity-50' : ''} ${className}`}
      style={{ backgroundColor: blockBackground(block), borderColor: visual.border, ...style, boxShadow: selected ? '0 0 0 3px var(--focus)' : style?.boxShadow }}
    >
      {children ?? <span aria-hidden="true">{visual.short}</span>}
      {block.content_purged && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-[var(--surface)] bg-[var(--danger)]" />}
    </button>
  )
}
