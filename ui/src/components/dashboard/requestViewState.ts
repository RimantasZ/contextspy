import { useEffect, useState } from 'react'

type DensityChoices = { sequenceCompact: boolean; conversations: Record<string, boolean> }

// In-memory only: choices follow this capture across SPA navigation, but a fresh
// page load starts Compact again. Never carry choices into another capture.
const densityBySession = new Map<string, DensityChoices>()

function choicesFor(sessionId: string): DensityChoices {
  return densityBySession.get(sessionId) ?? { sequenceCompact: true, conversations: {} }
}

export function useCompactRequestCards(sessionId: string) {
  const [state, setState] = useState(() => ({ sessionId, compact: choicesFor(sessionId).sequenceCompact }))
  const compact = state.sessionId === sessionId ? state.compact : choicesFor(sessionId).sequenceCompact

  function setCompact(value: boolean) {
    densityBySession.set(sessionId, { ...choicesFor(sessionId), sequenceCompact: value })
    setState({ sessionId, compact: value })
  }

  return [compact, setCompact] as const
}

export function useSelectedRequest(newestId: string | null) {
  const [selectedId, setSelectedId] = useState<string | null>(newestId)
  // Selection survives polling. A genuinely new head follows latest again.
  useEffect(() => { setSelectedId(newestId) }, [newestId])
  return [selectedId ?? newestId, setSelectedId] as const
}

export function useConversationCardDensity(sessionId: string) {
  const [state, setState] = useState(() => ({ sessionId, overrides: choicesFor(sessionId).conversations }))
  const overrides = state.sessionId === sessionId ? state.overrides : choicesFor(sessionId).conversations

  function setGroupCompact(groupKey: string, compact: boolean) {
    const next = { ...overrides, [groupKey]: compact }
    densityBySession.set(sessionId, { ...choicesFor(sessionId), conversations: next })
    setState({ sessionId, overrides: next })
  }

  return [(groupKey: string) => overrides[groupKey] ?? true, setGroupCompact] as const
}
