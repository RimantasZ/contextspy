import { SegmentedControl } from '../ui/SegmentedControl'

export function RequestDensityToggle({ compact, onChange, label = 'Request card detail' }: {
  compact: boolean; onChange: (value: boolean) => void; label?: string
}) {
  return <SegmentedControl
    label={label}
    value={compact ? 'compact' : 'detailed'}
    options={[{ value: 'compact', label: 'Compact' }, { value: 'detailed', label: 'Detailed' }]}
    onChange={(value) => onChange(value === 'compact')}
    size="short"
  />
}

export function RequestViewControls({ compact, onCompactChange, layout, onLayoutChange, groupCount }: {
  compact?: boolean
  onCompactChange?: (value: boolean) => void
  layout: 'sequence' | 'conversations'
  onLayoutChange: (value: 'sequence' | 'conversations') => void
  groupCount: number
}) {
  return <div className="flex flex-wrap items-center justify-end gap-2">
    {compact !== undefined && onCompactChange && <RequestDensityToggle compact={compact} onChange={onCompactChange} />}
    {(layout === 'conversations' || groupCount > 1) && <button type="button" className="app-button h-[34px] py-0" onClick={() => onLayoutChange(layout === 'sequence' ? 'conversations' : 'sequence')}>
      {layout === 'sequence' ? `${groupCount} conversations` : 'Show sequence'}
    </button>}
  </div>
}
