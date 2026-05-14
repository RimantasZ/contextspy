import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useWebSocket() {
  const qc = useQueryClient()
  const wsRef = useRef<WebSocket | null>(null)
  const retryDelayRef = useRef(1_000)

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const url = `${protocol}://${host}/api/ws`
    let cancelled = false

    function connect() {
      if (cancelled) return
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        retryDelayRef.current = 1_000
      }

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.event === 'new_request') {
            qc.invalidateQueries({ queryKey: ['requests'] })
            qc.invalidateQueries({ queryKey: ['stats'] })
            qc.invalidateQueries({ queryKey: ['stats', 'sessions-summary'] })
          } else if (msg.event === 'session_started' || msg.event === 'session_ended') {
            qc.invalidateQueries({ queryKey: ['sessions'] })
            qc.invalidateQueries({ queryKey: ['stats'] })
            qc.invalidateQueries({ queryKey: ['stats', 'sessions-summary'] })
          }
        } catch (_) {}
      }

      ws.onclose = () => {
        if (cancelled) return
        const delay = retryDelayRef.current
        retryDelayRef.current = Math.min(delay * 2, 30_000)
        setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      cancelled = true
      wsRef.current?.close()
    }
  }, [qc])
}
