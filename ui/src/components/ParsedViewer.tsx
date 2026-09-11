// Copyright 2026 Rimantas Zukaitis
// Compatibility viewer for consumers that still embed RawViewer directly.
// The request-detail route uses RequestWorkbench, but both surfaces share the
// same semantic theme, block maps, inspector, and content inspection behavior.
import { useState } from 'react'
import { useRequestBlocks } from '../api/hooks'
import { sortedBlocks } from '../lib/blockVisuals'
import { SegmentedControl } from './ui/SegmentedControl'
import { SearchableContentViewer } from './ui/SearchableContentViewer'
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
        <div className="p-3">
          <SearchableContentViewer title="Raw request payload" content={rawBody} emptyMessage="Raw content has been purged." maxHeight={600} />
        </div>
      ) : blocksQuery.isLoading ? (
        <p className="p-4 text-sm italic text-[var(--text-muted)]">Loading composition…</p>
      ) : blocks.length === 0 ? (
        <p className="p-4 text-sm italic text-[var(--text-muted)]">No block data available for this request.</p>
      ) : (
        <div className="workbench-grid grid items-start gap-3 bg-[var(--surface-muted)] p-3">
          <div className="min-w-0 space-y-3" data-workbench-primary-pane>
            <div className="surface min-w-0 overflow-hidden rounded-md border border-[var(--border)]">
              <div className="max-h-[560px] overflow-auto">
                {view === 'compact'
                  ? <CompactBlockMap blocks={blocks} selectedId={selectedId} density={26} grouping="sequence" onSelect={(block) => setSelectedId(block?.id ?? null)} />
                  : <ProportionalBlockMap blocks={blocks} selectedId={selectedId} density={26} onSelect={(block) => setSelectedId(block?.id ?? null)} />}
              </div>
              <BlockLegend blocks={blocks} />
            </div>
            {selected && (
              <SearchableContentViewer
                key={selected.id}
                title="Block content"
                content={selected.content_purged ? null : selected.content}
                emptyMessage={selected.content_purged
                  ? 'Content was purged, but its structure and token count are retained.'
                  : 'No content was captured for this structural block.'}
                maxHeight={420}
              />
            )}
          </div>
          <BlockInspector block={selected} blocks={blocks} onJump={jumpTo} onClear={() => setSelectedId(null)} />
        </div>
      )}
    </div>
  )
}
