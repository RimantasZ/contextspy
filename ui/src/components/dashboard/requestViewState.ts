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

function readConversationDensity(sessionId: string): Record<string, boolean> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(`contextspy.conversation-density:${sessionId}`) ?? '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'))
  } catch { return {} }
}

export function useConversationCardDensity(sessionId: string, defaultCompact: boolean) {
  const [state, setState] = useState(() => ({ sessionId, overrides: readConversationDensity(sessionId) }))
  const overrides = state.sessionId === sessionId ? state.overrides : readConversationDensity(sessionId)
  function setGroupCompact(groupKey: string, compact: boolean) {
    const next = { ...overrides, [groupKey]: compact }
    setState({ sessionId, overrides: next })
    try { localStorage.setItem(`contextspy.conversation-density:${sessionId}`, JSON.stringify(next)) } catch { /* storage unavailable */ }
  }
  return [(groupKey: string) => overrides[groupKey] ?? defaultCompact, setGroupCompact] as const
}
