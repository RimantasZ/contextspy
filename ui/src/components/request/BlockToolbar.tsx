import type { BlockVisual } from '../../lib/blockVisuals'
import { BLOCK_VISUALS } from '../../lib/blockVisuals'

export type GroupMode = 'sequence' | 'turn' | 'tool'

const FILTERS: BlockVisual[] = ['system', 'user', 'assistant', 'thinking', 'tool_call', 'tool_result', 'tool_definition', 'prefill', 'other']

export function BlockToolbar({
  available,
  active,
  search,
  grouping,
  hideZero,
  density,
  onToggleType,
  onSearch,
  onGrouping,
  onHideZero,
  onDensity,
  onLargest,
}: {
  available: Set<BlockVisual>
  active: Set<BlockVisual>
  search: string
  grouping: GroupMode
  hideZero: boolean
  density: number
  onToggleType: (visual: BlockVisual) => void
  onSearch: (value: string) => void
  onGrouping: (value: GroupMode) => void
  onHideZero: (value: boolean) => void
  onDensity: (value: number) => void
  onLargest: () => void
}) {
  return (
    <div className="space-y-2.5 border-b border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="min-w-[180px] flex-1 sm:max-w-xs">
          <span className="sr-only">Search request blocks</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="Search content or tool…"
            aria-label="Search request blocks"
            className="app-field w-full py-1.5"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          Group
          <select value={grouping} onChange={(event) => onGrouping(event.target.value as GroupMode)} className="app-field py-1.5">
            <option value="sequence">Sequence</option>
            <option value="turn">Turn</option>
            <option value="tool">Tool pair</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          Size
          <select value={density} onChange={(event) => onDensity(Number(event.target.value))} className="app-field py-1.5">
            <option value={22}>Smaller</option>
            <option value={26}>Default</option>
            <option value={30}>Larger</option>
          </select>
        </label>
        <label className="flex min-h-8 items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <input type="checkbox" checked={hideZero} onChange={(event) => onHideZero(event.target.checked)} />
          Hide zero-token
        </label>
        <button type="button" onClick={onLargest} className="app-button min-h-8 py-1 text-xs">Jump to largest</button>
      </div>
      <div className="flex flex-wrap gap-1.5" aria-label="Filter block types">
        {FILTERS.filter((visual) => available.has(visual)).map((visual) => {
          const item = BLOCK_VISUALS[visual]
          const enabled = active.has(visual)
          return (
            <button
              key={visual}
              type="button"
              aria-pressed={enabled}
              onClick={() => onToggleType(visual)}
              className={`min-h-7 rounded-full border px-2.5 text-[11px] font-medium ${enabled ? 'border-[var(--graphical-border)] text-[var(--block-ink)]' : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] opacity-60'}`}
              style={enabled ? { background: item.color, borderColor: item.border } : undefined}
            >
              <span className="font-bold" aria-hidden="true">{item.short}</span> {item.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
