import type { RequestBlock } from '../../api/client'
import { BLOCK_VISUALS, visualOf } from '../../lib/blockVisuals'

export function BlockLegend({ blocks }: { blocks: RequestBlock[] }) {
  const summary = new Map<string, { count: number; tokens: number }>()
  for (const block of blocks) {
    const visual = visualOf(block)
    const current = summary.get(visual) ?? { count: 0, tokens: 0 }
    current.count += 1
    current.tokens += Math.max(0, block.token_count)
    summary.set(visual, current)
  }
  const totalTokens = blocks.reduce((sum, block) => sum + Math.max(0, block.token_count), 0)

  return (
    <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px]">
      <span className="font-medium tabular-nums text-[var(--text)]">{blocks.length.toLocaleString()} blocks · {totalTokens.toLocaleString()} tokens</span>
      {[...summary.entries()].map(([visual, values]) => (
        <span key={visual} className="inline-flex items-center gap-1 text-[var(--text-muted)]">
          <span className="h-2.5 w-2.5 border border-[var(--graphical-border)]" style={{ background: BLOCK_VISUALS[visual as keyof typeof BLOCK_VISUALS].color }} />
          {BLOCK_VISUALS[visual as keyof typeof BLOCK_VISUALS].label} {values.count}
        </span>
      ))}
    </div>
  )
}
