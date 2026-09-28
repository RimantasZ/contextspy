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

export function normalizeServerTimestamp(timestamp: string): string {
  return timestamp.endsWith('Z') || /[+-]\d\d:\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`
}

export function formatRequestTime(timestamp: string): string {
  const utc = normalizeServerTimestamp(timestamp)
  return new Date(utc).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

/** Full date + time, e.g. "9/28/2026, 14:32:07". For detail views and tooltips. */
export function formatDateTime(timestamp: string): string {
  return new Date(normalizeServerTimestamp(timestamp)).toLocaleString(undefined, { hour12: false })
}

/** Compact date + time, e.g. "Sep 28, 14:32". For list rows and cards. */
export function formatDateTimeCompact(timestamp: string): string {
  return new Date(normalizeServerTimestamp(timestamp)).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** Time only, e.g. "14:32:07". For cards where the date is already implied. */
export function formatTimeShort(timestamp: string): string {
  return new Date(normalizeServerTimestamp(timestamp)).toLocaleString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

/** Unambiguous full date + time, e.g. "2026-09-28 14:32:07". For tooltips. */
export function formatDateTimeFull(timestamp: string): string {
  const d = new Date(normalizeServerTimestamp(timestamp))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
