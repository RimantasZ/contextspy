import { useEffect, useMemo, useState } from 'react'
import type { Request, RequestBlock } from '../../api/client'
import { useContextDiff, useRequestBlocks } from '../../api/hooks'
import type { ArrangementPreset } from '../../lib/blockArrangement'
import { ARRANGEMENT_PRESETS, buildWorkbenchBlockModel } from '../../lib/blockArrangement'
import type { BlockVisual } from '../../lib/blockVisuals'
import { BLOCK_VISUALS, visualOf } from '../../lib/blockVisuals'
import { SegmentedControl } from '../ui/SegmentedControl'
import { SearchableContentViewer } from '../ui/SearchableContentViewer'
import { BlockInspector } from './BlockInspector'
import { BlockLegend } from './BlockLegend'
import { BlockToolbar } from './BlockToolbar'
import type { ShowMode } from './BlockToolbar'
import { CompactBlockMap } from './CompactBlockMap'
import { ProportionalBlockMap } from './ProportionalBlockMap'

export type { ShowMode }
export type WorkbenchDirection = 'input' | 'output'
type WorkbenchView = 'compact' | 'proportional' | 'raw'

const ALL_VISUALS: BlockVisual[] = ['system', 'tool_definition', 'user', 'assistant', 'tool_call', 'tool_result', 'thinking', 'prefill', 'other']

function revealBlock(blockId: number, view: WorkbenchView, block: ScrollLogicalPosition) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const prefix = view === 'compact' ? 'block-tile' : 'block-segment'
    const element = document.getElementById(`${prefix}-${blockId}`)
    element?.scrollIntoView({ block, inline: 'nearest' })
    element?.focus({ preventScroll: true })
  }))
}

function rawPayload(request: Request, direction: WorkbenchDirection): string | null | undefined {
  if (direction === 'input') return request.request_body ?? request.canonical_request_body ?? request.raw_request_body
  return request.response_body ?? request.canonical_response_body ?? request.raw_response_body
}

function RawPayload({ request, direction }: { request: Request; direction: WorkbenchDirection }) {
  const content = rawPayload(request, direction)
  const [source, setSource] = useState<'payload' | 'wire' | 'events'>('payload')
  const hasWire = direction === 'input' && request.raw_request_body != null && request.raw_request_body !== content
  const hasEvents = direction === 'output' && (request.response_events?.length ?? 0) > 0
  const selectedSource = (source === 'wire' && !hasWire) || (source === 'events' && !hasEvents) ? 'payload' : source
  const shown = selectedSource === 'events' ? JSON.stringify(request.response_events) : selectedSource === 'wire' ? request.raw_request_body : content
  const title = selectedSource === 'events' ? 'Raw response events' : selectedSource === 'wire' ? 'Original wire request' : hasWire ? 'Canonical request payload' : `Raw ${direction === 'input' ? 'request' : 'response'} payload`

  useEffect(() => { setSource('payload') }, [direction])

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 border-b border-[var(--border)] px-3 py-2">
        <div className="flex gap-1">
          <button type="button" aria-pressed={selectedSource === 'payload'} onClick={() => setSource('payload')} className={`app-button min-h-8 py-1 text-xs ${selectedSource === 'payload' ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>{hasWire ? 'Canonical' : 'Payload'}</button>
          {hasWire && <button type="button" aria-pressed={selectedSource === 'wire'} onClick={() => setSource('wire')} className={`app-button min-h-8 py-1 text-xs ${selectedSource === 'wire' ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>Wire</button>}
          {hasEvents && <button type="button" aria-pressed={selectedSource === 'events'} onClick={() => setSource('events')} className={`app-button min-h-8 py-1 text-xs ${selectedSource === 'events' ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>Events</button>}
        </div>
      </div>
      <div className="p-3">
        <SearchableContentViewer
          key={`${direction}-${selectedSource}`}
          title={title}
          content={shown}
          emptyMessage="Raw content has been purged or was not captured."
          maxHeight={560}
        />
      </div>
    </div>
  )
}

export function RequestWorkbench({ request, activeDirection, onDirectionChange, parentRequestId = null, lineageLoading = false, showMode = 'all', onShowModeChange }: {
  request: Request
  activeDirection: WorkbenchDirection
  onDirectionChange: (direction: WorkbenchDirection) => void
  /** Lineage parent used as the "previous request" baseline for the Show control. */
  parentRequestId?: string | null
  lineageLoading?: boolean
  /** Owned by the page so it survives parent/child navigation. */
  showMode?: ShowMode
  onShowModeChange?: (mode: ShowMode) => void
}) {
  const blocksQuery = useRequestBlocks(request.id)
  const diffQuery = useContextDiff(request.id, parentRequestId)
  const [view, setView] = useState<WorkbenchView>('proportional')
  const [activeTypes, setActiveTypes] = useState<Set<BlockVisual>>(() => new Set(ALL_VISUALS))
  const [search, setSearch] = useState('')
  const [arrangement, setArrangement] = useState<ArrangementPreset>('sequence')
  const [hideZero, setHideZero] = useState(false)
  const [density, setDensity] = useState(26)
  const [selection, setSelection] = useState<Record<WorkbenchDirection, number | null>>({ input: null, output: null })

  const showDisabledReason = activeDirection === 'output' ? 'Available for request blocks only'
    : lineageLoading ? 'Loading comparison…'
      : !parentRequestId ? 'No previous request in this conversation to compare against'
        : diffQuery.isLoading ? 'Loading comparison…'
          : diffQuery.data ? null : 'Could not load comparison'
  const effectiveShow: ShowMode = showDisabledReason ? 'all' : showMode
  const newIds = useMemo(() => new Set(diffQuery.data?.new_child_block_ids ?? []), [diffQuery.data])

  const effectivePreset: ArrangementPreset = activeDirection === 'output' ? 'sequence' : arrangement
  const blockModel = useMemo(() => buildWorkbenchBlockModel(blocksQuery.data?.blocks ?? [], {
    direction: activeDirection,
    activeTypes,
    hideZero,
    query: search,
    arrangement: ARRANGEMENT_PRESETS[effectivePreset],
    onlyIds: effectiveShow === 'new' ? newIds : null,
  }), [activeDirection, activeTypes, blocksQuery.data?.blocks, effectivePreset, effectiveShow, hideZero, newIds, search])
  const { allBlocks, available, visibleBlocks } = blockModel
  const dimmedIds = useMemo(() => effectiveShow === 'highlight'
    ? new Set(visibleBlocks.filter((block) => !newIds.has(block.id)).map((block) => block.id))
    : undefined, [effectiveShow, newIds, visibleBlocks])
  const tokenTotals = blocksQuery.data?.token_totals?.[activeDirection] ?? {}

  const selectedId = selection[activeDirection]
  const selected = allBlocks.find((block) => block.id === selectedId) ?? null

  useEffect(() => {
    if (selectedId != null && !visibleBlocks.some((block) => block.id === selectedId)) {
      setSelection((current) => ({ ...current, [activeDirection]: null }))
    }
  }, [activeDirection, selectedId, visibleBlocks])

  function select(block: RequestBlock | null) {
    setSelection((current) => ({ ...current, [activeDirection]: block?.id ?? null }))
  }

  function jumpTo(targetId: number) {
    const target = allBlocks.find((block) => block.id === targetId)
    if (!target) return
    onDirectionChange(target.direction)
    setSearch('')
    setActiveTypes((current) => new Set(current).add(visualOf(target)))
    if (target.token_count <= 0) setHideZero(false)
    if (effectiveShow === 'new' && !newIds.has(target.id)) onShowModeChange?.('all')
    setSelection((current) => ({ ...current, [target.direction]: target.id }))
    revealBlock(target.id, view, 'nearest')
  }

  function jumpLargest() {
    const largest = visibleBlocks.reduce<RequestBlock | null>((winner, block) => !winner || block.token_count > winner.token_count ? block : winner, null)
    if (!largest) return
    select(largest)
    revealBlock(largest.id, view, 'center')
  }

  return (
    <section className="surface overflow-hidden rounded-lg border border-[var(--border)]" aria-labelledby="workbench-title">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <h2 id="workbench-title" className="hidden text-sm font-semibold sm:block">Composition</h2>
          <SegmentedControl
            label="Request direction"
            value={activeDirection}
            onChange={onDirectionChange}
            options={[
              { value: 'input', label: 'Request', count: request.tokens_total_input },
              { value: 'output', label: 'Response', count: request.tokens_total_output },
            ]}
          />
        </div>
        <SegmentedControl
          label="Composition view"
          value={view}
          onChange={setView}
          options={[
            { value: 'proportional', label: 'Default' },
            { value: 'compact', label: 'Compact' },
            { value: 'raw', label: 'Raw' },
          ]}
        />
      </div>

      {view === 'raw' ? (
        <RawPayload request={request} direction={activeDirection} />
      ) : blocksQuery.isLoading ? (
        <div className="p-10 text-center text-sm text-[var(--text-muted)]">Loading composition…</div>
      ) : blocksQuery.error ? (
        <div className="p-10 text-center text-sm text-[var(--danger)]">Could not load request blocks.</div>
      ) : (
        <>
          <BlockToolbar
            available={available}
            active={activeTypes}
            tokenTotals={tokenTotals}
            search={search}
            arrangement={effectivePreset}
            arrangementDisabled={activeDirection === 'output'}
            hideZero={hideZero}
            density={density}
            showMode={effectiveShow}
            showDisabledReason={showDisabledReason}
            onShowMode={onShowModeChange}
            onToggleType={(visual) => setActiveTypes((current) => {
              const next = new Set(current)
              if (next.has(visual)) next.delete(visual); else next.add(visual)
              return next
            })}
            onSearch={setSearch}
            onArrangement={setArrangement}
            onHideZero={setHideZero}
            onDensity={setDensity}
            onLargest={jumpLargest}
          />
          <div className="workbench-grid grid min-w-0 items-start gap-3 bg-[var(--surface-muted)] p-3">
            <div className="min-w-0 space-y-3" data-workbench-primary-pane>
              <div className="surface min-w-0 overflow-hidden rounded-md border border-[var(--border)]">
                <div className="max-h-[480px] min-h-32 overflow-auto">
                  {view === 'compact' ? (
                    <CompactBlockMap blocks={visibleBlocks} selectedId={selectedId} density={density} grouping={ARRANGEMENT_PRESETS[effectivePreset].grouping} dimmedIds={dimmedIds} onSelect={select} />
                  ) : (
                    <ProportionalBlockMap blocks={visibleBlocks} selectedId={selectedId} density={density} dimmedIds={dimmedIds} onSelect={select} />
                  )}
                </div>
                <BlockLegend blocks={visibleBlocks} />
              </div>
              {selected && (
                <SearchableContentViewer
                  key={selected.id}
                  title={`${BLOCK_VISUALS[visualOf(selected)].label} content`}
                  content={selected.content_purged ? null : selected.content}
                  emptyMessage={selected.content_purged
                    ? 'Content was purged, but its structure and token count are retained.'
                    : 'No content was captured for this structural block.'}
                  maxHeight={420}
                />
              )}
            </div>
            <BlockInspector block={selected} blocks={allBlocks} onJump={jumpTo} onClear={() => select(null)} />
          </div>
        </>
      )}
    </section>
  )
}
