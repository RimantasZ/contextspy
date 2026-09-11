import { useEffect, useState } from 'react'
import type { TokenWindowResponse } from '../../../api/client'
import { tokenizeApi } from '../../../api/client'

const CONTENT_CACHE_LIMIT = 8
const WINDOWS_PER_CONTENT = 4
const tokenWindowCache = new Map<string, Map<number, TokenWindowResponse>>()

function cachedWindow(text: string, offset: number): TokenWindowResponse | null {
  return tokenWindowCache.get(text)?.get(offset) ?? null
}

function remember(text: string, offset: number, value: TokenWindowResponse) {
  const windows = tokenWindowCache.get(text) ?? new Map<number, TokenWindowResponse>()
  windows.delete(offset)
  windows.set(offset, value)
  while (windows.size > WINDOWS_PER_CONTENT) windows.delete(windows.keys().next().value as number)
  tokenWindowCache.delete(text)
  tokenWindowCache.set(text, windows)
  while (tokenWindowCache.size > CONTENT_CACHE_LIMIT) tokenWindowCache.delete(tokenWindowCache.keys().next().value as string)
}

interface RequestState {
  text: string
  offset: number
  result: TokenWindowResponse | null
  error: boolean
}

export function useTokenWindow(text: string, enabled: boolean) {
  const [target, setTarget] = useState({ text, offset: 0 })
  const [requestState, setRequestState] = useState<RequestState>({ text, offset: 0, result: null, error: false })
  const requestedOffset = target.text === text ? target.offset : 0
  const cached = cachedWindow(text, requestedOffset)
  const matchingState = requestState.text === text && requestState.offset === requestedOffset ? requestState : null
  const result = cached ?? matchingState?.result ?? null
  const error = matchingState?.error ?? false

  useEffect(() => {
    if (!enabled || cachedWindow(text, requestedOffset)) return
    const controller = new AbortController()
    tokenizeApi.window(text, requestedOffset, controller.signal)
      .then((response) => {
        remember(text, requestedOffset, response)
        setRequestState({ text, offset: requestedOffset, result: response, error: false })
      })
      .catch((reason: unknown) => {
        if ((reason instanceof DOMException || reason instanceof Error) && reason.name === 'AbortError') return
        setRequestState({ text, offset: requestedOffset, result: null, error: true })
      })
    return () => controller.abort()
  }, [enabled, requestedOffset, text])

  const loading = enabled && result == null && !error
  const tokenCount = result?.segments.reduce((total, segment) => total + segment.token_count, 0) ?? 0
  const truncated = Boolean(result?.truncated_before || result?.truncated_after)

  function moveToViewport(viewport: HTMLDivElement | null) {
    if (!viewport || text.length === 0) return
    const center = viewport.scrollTop + viewport.clientHeight / 2
    const ratio = viewport.scrollHeight > 0 ? center / viewport.scrollHeight : 0
    const targetOffset = Math.round(Math.max(0, Math.min(1, ratio)) * text.length)
    const windowLength = result ? Math.max(1, result.window_end - result.window_start) : 1
    setTarget({ text, offset: Math.max(0, Math.min(text.length, targetOffset - Math.floor(windowLength / 2))) })
  }

  return { result, loading, error, tokenCount, truncated, requestedOffset, moveToViewport }
}
