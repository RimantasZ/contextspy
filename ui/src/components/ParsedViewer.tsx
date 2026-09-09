// Copyright 2026 Rimantas Zukaitis
// Compatibility viewer for consumers that still embed RawViewer directly.
// The request-detail route uses RequestWorkbench, but both surfaces share the
// same semantic theme, block maps, inspector, and lazy tokenization behavior.
import { useState } from 'react'
import { useRequestBlocks } from '../api/hooks'
import { sortedBlocks } from '../lib/blockVisuals'
import { SegmentedControl } from './ui/SegmentedControl'
import { BlockInspector } from './request/BlockInspector'
import { BlockLegend } from './request/BlockLegend'
import { CompactBlockMap } from './request/CompactBlockMap'
import { ProportionalBlockMap } from './request/ProportionalBlockMap'

interface Props {
  requestId: string
  rawBody: string | null | undefined
  totalInputTokens?: number | null
}

type View = 'compact' | 'proportional' | 'raw'

export function ParsedViewer({ requestId, rawBody, totalInputTokens }: Props) {
  const blocksQuery = useRequestBlocks(requestId)
  const [view, setView] = useState<View>('compact')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const blocks = sortedBlocks((blocksQuery.data?.blocks ?? []).filter((block) => block.direction === 'input'))
  const selected = blocks.find((block) => block.id === selectedId) ?? null

  let rawPretty = rawBody ?? ''
  if (rawBody) {
    try { rawPretty = JSON.stringify(JSON.parse(rawBody), null, 2) } catch { /* preserve non-JSON body */ }
  }

  function jumpTo(targetId: number) {
    if (!blocks.some((block) => block.id === targetId)) return
    setSelectedId(targetId)
    requestAnimationFrame(() => document.getElementById(view === 'compact' ? `block-tile-${targetId}` : `block-segment-${targetId}`)?.scrollIntoView({ block: 'nearest' }))
  }

  return (
    <div className="surface min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] p-3">
        <SegmentedControl label="Request view" value={view} onChange={setView} options={[
          { value: 'compact', label: 'Compact' },
          { value: 'proportional', label: 'Proportional' },
          { value: 'raw', label: 'Raw' },
        ]} />
        {totalInputTokens != null && <span className="text-xs tabular-nums text-[var(--text-muted)]">{totalInputTokens.toLocaleString()} tokens</span>}
      </div>

      {view === 'raw' ? (
        rawBody == null ? <p className="p-4 text-sm italic text-[var(--text-muted)]">Raw content has been purged.</p> : <pre className="code-surface max-h-[600px] overflow-auto p-4 text-xs [overflow-wrap:anywhere] [white-space:pre-wrap]">{rawPretty}</pre>
      ) : blocksQuery.isLoading ? (
        <p className="p-4 text-sm italic text-[var(--text-muted)]">Loading composition…</p>
      ) : blocks.length === 0 ? (
        <p className="p-4 text-sm italic text-[var(--text-muted)]">No block data available for this request.</p>
      ) : (
        <div className="workbench-grid grid gap-3 bg-[var(--surface-muted)] p-3">
          <div className="surface min-w-0 overflow-hidden rounded-md border border-[var(--border)]">
            <div className="max-h-[560px] overflow-auto">
              {view === 'compact'
                ? <CompactBlockMap blocks={blocks} selectedId={selectedId} density={26} grouping="sequence" onSelect={(block) => setSelectedId(block?.id ?? null)} />
                : <ProportionalBlockMap blocks={blocks} selectedId={selectedId} onSelect={(block) => setSelectedId(block?.id ?? null)} />}
            </div>
            <BlockLegend blocks={blocks} />
          </div>
          <BlockInspector block={selected} onJump={jumpTo} onClear={() => setSelectedId(null)} />
        </div>
      )}
    </div>
  )
}
