export function RequestViewControls({ compact, onCompactChange, layout, onLayoutChange, groupCount }: {
  compact: boolean
  onCompactChange: (value: boolean) => void
  layout: 'sequence' | 'conversations'
  onLayoutChange: (value: 'sequence' | 'conversations') => void
  groupCount: number
}) {
  return <div className="flex flex-wrap items-center justify-end gap-2">
    <label className="app-button flex cursor-pointer items-center gap-2 text-xs">
      <input type="checkbox" checked={compact} onChange={(event) => onCompactChange(event.target.checked)} />
      Compact mode
    </label>
    {(layout === 'conversations' || groupCount > 1) && <button type="button" className="app-button" onClick={() => onLayoutChange(layout === 'sequence' ? 'conversations' : 'sequence')}>
      {layout === 'sequence' ? `${groupCount} conversations` : 'Show sequence'}
    </button>}
  </div>
}
