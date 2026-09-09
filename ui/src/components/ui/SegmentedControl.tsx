export interface Segment<T extends string> {
  value: T
  label: string
  count?: number
}

export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: Segment<T>[]
  onChange: (value: T) => void
}) {
  return (
    <div className="inline-flex min-w-0 rounded-md border border-[var(--border)] bg-[var(--surface-muted)] p-0.5" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={`min-h-8 rounded px-2.5 text-xs font-medium transition-colors sm:px-3 ${
            value === option.value ? 'bg-[var(--surface-elevated)] text-[var(--text)] shadow-sm' : 'text-[var(--text-muted)] hover:text-[var(--text)]'
          }`}
        >
          {option.label}
          {option.count != null && <span className="ml-1 tabular-nums opacity-60">{option.count.toLocaleString()}</span>}
        </button>
      ))}
    </div>
  )
}
