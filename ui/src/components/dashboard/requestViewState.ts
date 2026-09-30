import { useEffect, useState } from 'react'

type DensityChoices = { sequenceCompact: boolean; conversations: Record<string, boolean> }

// In-memory only: choices follow this capture across SPA navigation, but a fresh
// page load starts Compact again. Never carry choices into another capture.
const densityBySession = new Map<string, DensityChoices>()
const MAX_DENSITY_SESSIONS = 32

function choicesFor(sessionId: string): DensityChoices {
  const saved = densityBySession.get(sessionId)
  if (!saved) return { sequenceCompact: true, conversations: {} }
  // A read counts as use, so the oldest inactive session is evicted first.
  densityBySession.delete(sessionId)
  densityBySession.set(sessionId, saved)
  return saved
}

function rememberChoices(sessionId: string, choices: DensityChoices) {
  densityBySession.delete(sessionId)
  densityBySession.set(sessionId, choices)
  if (densityBySession.size > MAX_DENSITY_SESSIONS) {
    densityBySession.delete(densityBySession.keys().next().value!)
  }
}

export function useCompactRequestCards(sessionId: string) {
  const [state, setState] = useState(() => ({ sessionId, compact: choicesFor(sessionId).sequenceCompact }))
  const compact = state.sessionId === sessionId ? state.compact : choicesFor(sessionId).sequenceCompact

  function setCompact(value: boolean) {
    rememberChoices(sessionId, { ...choicesFor(sessionId), sequenceCompact: value })
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
    rememberChoices(sessionId, { ...choicesFor(sessionId), conversations: next })
    setState({ sessionId, overrides: next })
  }

  return [(groupKey: string) => overrides[groupKey] ?? true, setGroupCompact] as const
}
