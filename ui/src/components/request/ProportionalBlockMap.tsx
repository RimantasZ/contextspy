import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { RequestBlock } from '../../api/client'
import { layoutProportionalBlocks } from '../../lib/blockLayout'
import { BLOCK_VISUALS, blockAccessibleName, blockBackground, visualOf } from '../../lib/blockVisuals'
import type { GroupMode } from './BlockToolbar'

const BLOCK_GAP_PX = 4
const CANVAS_HORIZONTAL_PADDING_PX = 24
const FALLBACK_ROW_CAPACITY = 12

export function ProportionalBlockMap({ blocks, selectedId, density, grouping = 'sequence', onSelect }: {
  blocks: RequestBlock[]
  selectedId: number | null
  density: number
  grouping?: GroupMode
  onSelect: (block: RequestBlock | null) => void
}) {
  const canvasRef = useRef<HTMLDivElement>(null)
  const [rowCapacity, setRowCapacity] = useState(FALLBACK_ROW_CAPACITY)

  useLayoutEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const updateCapacity = () => {
      const available = canvas.clientWidth - CANVAS_HORIZONTAL_PADDING_PX
      if (available <= 0) return
      const next = Math.max(1, Math.floor((available + BLOCK_GAP_PX) / (density + BLOCK_GAP_PX)))
      setRowCapacity((current) => current === next ? current : next)
    }

    updateCapacity()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(updateCapacity)
      observer.observe(canvas)
      return () => observer.disconnect()
    }
    window.addEventListener('resize', updateCapacity)
    return () => window.removeEventListener('resize', updateCapacity)
  }, [density])

  const layout = useMemo(() => layoutProportionalBlocks(blocks, rowCapacity, grouping === 'size' ? 'size' : 'sequence'), [blocks, grouping, rowCapacity])
  const items = layout.rows.flatMap((row) => row.items)

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, blockId: number) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onSelect(null)
      return
    }
    if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()

    const currentIndex = items.findIndex((item) => item.blockId === blockId)
    const current = items[currentIndex]
    let next = current

    if (event.key === 'Home') next = items[0]
    if (event.key === 'End') next = items[items.length - 1]
    if (event.key === 'ArrowRight') next = items[Math.min(items.length - 1, currentIndex + 1)]
    if (event.key === 'ArrowLeft') next = items[Math.max(0, currentIndex - 1)]
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && current) {
      const currentRowIndex = layout.rows.findIndex((row) => row.items.some((item) => item.blockId === blockId))
      const targetRow = layout.rows[currentRowIndex + (event.key === 'ArrowDown' ? 1 : -1)]
      if (targetRow?.items.length) {
        next = targetRow.items.reduce((nearest, item) =>
          Math.abs(item.columnStart - current.columnStart) < Math.abs(nearest.columnStart - current.columnStart) ? item : nearest)
      }
    }

    if (!next) return
    onSelect(next.block)
    document.getElementById(`block-segment-${next.blockId}`)?.focus()
  }

  if (blocks.length === 0) return <div className="p-8 text-center text-sm text-[var(--text-muted)]">No blocks match these filters.</div>

  return (
    <div ref={canvasRef} data-block-map role="group" aria-label="Token-proportional block map" className="space-y-1 p-3">
      {layout.rows.map((row) => (
        <div
          key={row.index}
          className="grid gap-1"
          style={{ gridTemplateColumns: `repeat(${layout.capacity}, ${density}px)`, gridAutoRows: `${density}px` }}
          aria-label={`Row ${row.index + 1}, ${row.tokens.toLocaleString()} tokens, ${row.capacity - row.usedColumns} unused slots`}
        >
          {row.items.map((item) => {
            const visual = visualOf(item.block)
            const blockStyle = BLOCK_VISUALS[visual]
            const selected = selectedId === item.blockId
            return (
              <button
                key={item.blockId}
                id={`block-segment-${item.blockId}`}
                data-block-map-item
                data-block-id={item.blockId}
                type="button"
                tabIndex={selected || (selectedId == null && items[0]?.blockId === item.blockId) ? 0 : -1}
                aria-label={blockAccessibleName(item.block)}
                aria-pressed={selected}
                title={blockAccessibleName(item.block)}
                onClick={() => onSelect(selected ? null : item.block)}
                onKeyDown={(event) => onKeyDown(event, item.blockId)}
                className="composition-block flex min-w-0 items-center justify-center overflow-hidden border-[var(--graphical-border)] text-center"
                style={{
                  gridColumn: `${item.columnStart + 1} / span ${item.span}`,
                  backgroundColor: blockBackground(item.block),
                  borderColor: blockStyle.border,
                  boxShadow: selected ? '0 0 0 3px var(--focus)' : undefined,
                }}
              >
                <span className="min-w-0 truncate px-1" aria-hidden="true">
                  {blockStyle.short}{item.span > 1 && <> · {item.block.token_count.toLocaleString()}</>}
                </span>
                {item.block.content_purged && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-[var(--surface)] bg-[var(--danger)]" />}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
