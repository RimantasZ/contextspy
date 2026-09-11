export function formatRequestDuration(milliseconds: number | null): string {
  if (milliseconds == null || milliseconds < 0) return '—'
  return milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds / 1000).toFixed(1)}s`
}

export function formatElapsedDuration(milliseconds: number | null, nullLabel = '—'): string {
  if (milliseconds == null) return nullLabel
  if (milliseconds < 0) return '—'
  const seconds = Math.floor(milliseconds / 1000)
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

export function formatPercent(numerator: number, denominator?: number | null, digits = 1): string | null {
  return denominator && denominator > 0 ? `${((numerator / denominator) * 100).toFixed(digits)}%` : null
}

export function formatRequestTime(timestamp: string): string {
  const utc = timestamp.endsWith('Z') || timestamp.includes('+') ? timestamp : `${timestamp}Z`
  return new Date(utc).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
