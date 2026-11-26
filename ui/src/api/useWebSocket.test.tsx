import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from './useWebSocket'

afterEach(() => vi.unstubAllGlobals())

describe('useWebSocket', () => {
  it('refreshes session data when a session is reopened', () => {
    const sockets: { onmessage?: (event: { data: string }) => void }[] = []
    vi.stubGlobal('WebSocket', class {
      onmessage?: (event: { data: string }) => void
      constructor() { sockets.push(this) }
      close() {}
    })
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    renderHook(() => useWebSocket(), { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> })

    sockets[0].onmessage?.({ data: JSON.stringify({ event: 'session_reopened', data: {} }) })

    const keys = invalidate.mock.calls.map((call) => (call[0] as { queryKey: string[] }).queryKey[0])
    expect(keys).toEqual(expect.arrayContaining(['sessions', 'session', 'stats']))
  })
})
