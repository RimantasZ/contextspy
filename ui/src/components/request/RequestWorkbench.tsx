import { useEffect, useMemo, useState } from 'react'
import type { Request, RequestBlock } from '../../api/client'
import { useRequestBlocks } from '../../api/hooks'
import type { BlockVisual } from '../../lib/blockVisuals'
import { BLOCK_VISUALS, sortedBlocks, visualOf } from '../../lib/blockVisuals'
import { SegmentedControl } from '../ui/SegmentedControl'
import { SearchableContentViewer } from '../ui/SearchableContentViewer'
import { BlockInspector } from './BlockInspector'
import { BlockLegend } from './BlockLegend'
import { BlockToolbar } from './BlockToolbar'
import type { GroupMode } from './BlockToolbar'
import { CompactBlockMap } from './CompactBlockMap'
import { ProportionalBlockMap } from './ProportionalBlockMap'

export type WorkbenchDirection = 'input' | 'output'
type WorkbenchView = 'compact' | 'proportional' | 'raw'

const ALL_VISUALS: BlockVisual[] = ['system', 'tool_definition', 'user', 'assistant', 'tool_call', 'tool_result', 'thinking', 'prefill', 'other']

function rawPayload(request: Request, direction: WorkbenchDirection): string | null | undefined {
  if (direction === 'input') return request.request_body ?? request.canonical_request_body ?? request.raw_request_body
  return request.response_body ?? request.canonical_response_body ?? request.raw_response_body
}

function RawPayload({ request, direction }: { request: Request; direction: WorkbenchDirection }) {
  const content = rawPayload(request, direction)
  const [source, setSource] = useState<'payload' | 'events'>('payload')
  const hasEvents = direction === 'output' && (request.response_events?.length ?? 0) > 0
  const shown = source === 'events' ? JSON.stringify(request.response_events) : content
  const title = source === 'events' ? 'Raw response events' : `Raw ${direction === 'input' ? 'request' : 'response'} payload`

  useEffect(() => { setSource('payload') }, [direction])

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 border-b border-[var(--border)] px-3 py-2">
        <div className="flex gap-1">
          <button type="button" onClick={() => setSource('payload')} className={`app-button min-h-8 py-1 text-xs ${source === 'payload' ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>Payload</button>
          {hasEvents && <button type="button" onClick={() => setSource('events')} className={`app-button min-h-8 py-1 text-xs ${source === 'events' ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : ''}`}>Events</button>}
        </div>
      </div>
      <div className="p-3">
        <SearchableContentViewer
          key={`${direction}-${source}`}
          title={title}
          content={shown}
          emptyMessage="Raw content has been purged or was not captured."
          maxHeight={560}
        />
      </div>
    </div>
  )
}

export function RequestWorkbench({ request, activeDirection, onDirectionChange }: {
  request: Request
  activeDirection: WorkbenchDirection
  onDirectionChange: (direction: WorkbenchDirection) => void
}) {
  const blocksQuery = useRequestBlocks(request.id)
  const [view, setView] = useState<WorkbenchView>('compact')
  const [activeTypes, setActiveTypes] = useState<Set<BlockVisual>>(() => new Set(ALL_VISUALS))
  const [search, setSearch] = useState('')
  const [grouping, setGrouping] = useState<GroupMode>('sequence')
  const [hideZero, setHideZero] = useState(false)
  const [density, setDensity] = useState(26)
  const [selection, setSelection] = useState<Record<WorkbenchDirection, number | null>>({ input: null, output: null })

  const allBlocks = sortedBlocks(blocksQuery.data?.blocks ?? [])
  const directionBlocks = allBlocks.filter((block) => block.direction === activeDirection)
  const available = new Set(directionBlocks.map(visualOf))
  const query = search.trim().toLocaleLowerCase()
  const visibleBlocks = useMemo(() => directionBlocks.filter((block) => {
    if (hideZero && block.token_count <= 0) return false
    if (!activeTypes.has(visualOf(block))) return false
    if (!query) return true
    return [block.content, block.tool_name, block.block_type, block.category, block.tool_call_id]
      .some((value) => String(value ?? '').toLocaleLowerCase().includes(query))
  }), [directionBlocks, activeTypes, hideZero, query])

  const selectedId = selection[activeDirection]
  const selected = allBlocks.find((block) => block.id === selectedId) ?? null

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
    setSelection((current) => ({ ...current, [target.direction]: target.id }))
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.getElementById(view === 'compact' ? `block-tile-${target.id}` : `block-segment-${target.id}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }))
  }

  function jumpLargest() {
    const largest = visibleBlocks.reduce<RequestBlock | null>((winner, block) => !winner || block.token_count > winner.token_count ? block : winner, null)
    if (!largest) return
    select(largest)
    requestAnimationFrame(() => document.getElementById(view === 'compact' ? `block-tile-${largest.id}` : `block-segment-${largest.id}`)?.scrollIntoView({ block: 'center' }))
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
            { value: 'compact', label: 'Compact' },
            { value: 'proportional', label: 'Proportional' },
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
            search={search}
            grouping={grouping}
            hideZero={hideZero}
            density={density}
            onToggleType={(visual) => setActiveTypes((current) => {
              const next = new Set(current)
              if (next.has(visual)) next.delete(visual); else next.add(visual)
              return next
            })}
            onSearch={setSearch}
            onGrouping={setGrouping}
            onHideZero={setHideZero}
            onDensity={setDensity}
            onLargest={jumpLargest}
          />
          <div className="workbench-grid grid min-w-0 items-start gap-3 bg-[var(--surface-muted)] p-3">
            <div className="min-w-0 space-y-3" data-workbench-primary-pane>
              <div className="surface min-w-0 overflow-hidden rounded-md border border-[var(--border)]">
                <div className="max-h-[480px] min-h-32 overflow-auto">
                  {view === 'compact' ? (
                    <CompactBlockMap blocks={visibleBlocks} selectedId={selectedId} density={density} grouping={grouping} onSelect={select} />
                  ) : (
                    <ProportionalBlockMap blocks={visibleBlocks} selectedId={selectedId} density={density} onSelect={select} />
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
