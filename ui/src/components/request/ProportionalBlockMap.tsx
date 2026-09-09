import type { KeyboardEvent } from 'react'
import type { RequestBlock } from '../../api/client'
import { layoutProportionalBlocks } from '../../lib/blockLayout'
import { BLOCK_VISUALS, blockAccessibleName, visualOf } from '../../lib/blockVisuals'

export function ProportionalBlockMap({ blocks, selectedId, onSelect }: {
  blocks: RequestBlock[]
  selectedId: number | null
  onSelect: (block: RequestBlock | null) => void
}) {
  const layout = layoutProportionalBlocks(blocks)
  const unique = blocks.filter((block) => block.token_count > 0)

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, blockId: number) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onSelect(null)
      return
    }
    if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const index = unique.findIndex((block) => block.id === blockId)
    const delta = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? unique.length - 1 : Math.max(0, Math.min(unique.length - 1, index + delta))
    const next = unique[nextIndex]
    onSelect(next)
    document.querySelector<HTMLButtonElement>(`#block-segment-${next.id}`)?.focus()
  }

  if (layout.rows.length === 0 && layout.zeroTokenBlocks.length === 0) {
    return <div className="p-8 text-center text-sm text-[var(--text-muted)]">No blocks match these filters.</div>
  }

  return (
    <div data-block-map role="group" aria-label="Token-proportional block map" className="space-y-1.5 p-3">
      {layout.rows.map((row) => (
        <div key={row.index} className="relative h-8 overflow-visible rounded-[4px] bg-[var(--surface-muted)]" aria-label={`Row ${row.index + 1}, ${row.usedTokens.toLocaleString()} of ${layout.capacity.toLocaleString()} tokens`}>
          {row.segments.map((segment) => {
            const visual = visualOf(segment.block)
            const selected = selectedId === segment.blockId
            return (
              <button
                key={`${segment.blockId}-${segment.segmentIndex}`}
                id={segment.segmentIndex === 0 ? `block-segment-${segment.blockId}` : undefined}
                data-block-map-item={segment.segmentIndex === 0 ? '' : undefined}
                type="button"
                tabIndex={segment.segmentIndex === 0 && (selected || (selectedId == null && unique[0]?.id === segment.blockId)) ? 0 : -1}
                aria-label={`${blockAccessibleName(segment.block)}${segment.segmentIndex > 0 ? `, continuation ${segment.segmentIndex + 1}` : ''}`}
                aria-pressed={selected}
                title={`${blockAccessibleName(segment.block)} · ${segment.tokens.toLocaleString()} tokens in this row`}
                onClick={() => onSelect(selected ? null : segment.block)}
                onKeyDown={(event) => onKeyDown(event, segment.blockId)}
                className="proportional-segment absolute top-0 h-8 overflow-hidden border-y border-r border-[var(--graphical-border)] text-left text-[9px] font-bold text-[var(--block-ink)] first:border-l"
                style={{
                  left: `${segment.leftPct}%`,
                  width: `${segment.widthPct}%`,
                  minWidth: 0,
                  background: BLOCK_VISUALS[visual].color,
                  borderColor: BLOCK_VISUALS[visual].border,
                  boxShadow: selected ? 'inset 0 0 0 2px var(--focus)' : undefined,
                  zIndex: selected ? 2 : 1,
                }}
              >
                {segment.widthPct >= 8 && <span className="block truncate px-1">{BLOCK_VISUALS[visual].short} · {segment.tokens.toLocaleString()}</span>}
              </button>
            )
          })}
          <span className="pointer-events-none absolute right-1 top-0.5 text-[9px] text-[var(--text-muted)]">{row.usedTokens.toLocaleString()}</span>
        </div>
      ))}
      {layout.zeroTokenBlocks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 pt-1" aria-label="Zero-token block markers">
          <span className="mr-1 text-[10px] text-[var(--text-muted)]">Zero-token</span>
          {layout.zeroTokenBlocks.map((block) => {
            const selected = selectedId === block.id
            return (
              <button
                key={block.id}
                id={`block-segment-${block.id}`}
                type="button"
                aria-label={blockAccessibleName(block)}
                aria-pressed={selected}
                onClick={() => onSelect(selected ? null : block)}
                className="h-4 w-1.5 border border-[var(--graphical-border)]"
                style={{
                  background: BLOCK_VISUALS[visualOf(block)].color,
                  borderColor: BLOCK_VISUALS[visualOf(block)].border,
                  boxShadow: selected ? '0 0 0 2px var(--focus)' : undefined,
                }}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
