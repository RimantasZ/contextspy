import { useEffect, useState } from 'react'

const COMPACT_KEY = 'contextspy.compact-request-cards'

export function useCompactRequestCards() {
  const [compact, setCompact] = useState(() => {
    try { return localStorage.getItem(COMPACT_KEY) !== 'false' } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem(COMPACT_KEY, String(compact)) } catch { /* storage unavailable */ }
  }, [compact])
  return [compact, setCompact] as const
}

export function useSelectedRequest(newestId: string | null) {
  const [selectedId, setSelectedId] = useState<string | null>(newestId)
  // Selection survives polling. A genuinely new head follows latest again.
  useEffect(() => { setSelectedId(newestId) }, [newestId])
  return [selectedId ?? newestId, setSelectedId] as const
}
