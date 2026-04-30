import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { sessionsApi, requestsApi, statsApi, proxyApi } from './client'

// ---- Sessions -------------------------------------------------------------

export function useSessions() {
  return useQuery({
    queryKey: ['sessions'],
    queryFn: () => sessionsApi.list(),
    refetchInterval: 10_000,
  })
}

export function useSession(id: string) {
  return useQuery({
    queryKey: ['session', id],
    queryFn: () => sessionsApi.get(id),
    enabled: !!id,
  })
}

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => sessionsApi.create(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function useEndSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => sessionsApi.end(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useDeleteSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => sessionsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

// ---- Requests -------------------------------------------------------------

export function useRequests(params: { session_id?: string; provider?: string; agent?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ['requests', params],
    queryFn: () => requestsApi.list(params),
    refetchInterval: 5_000,
  })
}

export function useRequest(id: string) {
  return useQuery({
    queryKey: ['request', id],
    queryFn: () => requestsApi.get(id),
    enabled: !!id,
  })
}

// ---- Stats ----------------------------------------------------------------

export function useStatsOverview() {
  return useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: () => statsApi.overview(),
    refetchInterval: 5_000,
  })
}

export function useStatsSession(sessionId: string) {
  return useQuery({
    queryKey: ['stats', 'session', sessionId],
    queryFn: () => statsApi.session(sessionId),
    enabled: !!sessionId,
    refetchInterval: 5_000,
  })
}

export function useTimeline(sessionId: string | undefined, bucket: string) {
  return useQuery({
    queryKey: ['timeline', sessionId, bucket],
    queryFn: () => statsApi.timeline({ session_id: sessionId, bucket }),
    refetchInterval: 10_000,
  })
}

// ---- Proxy ----------------------------------------------------------------

export function useProxyStatus() {
  return useQuery({
    queryKey: ['proxy', 'status'],
    queryFn: () => proxyApi.status(),
    refetchInterval: 5_000,
  })
}

export function useProxyStart() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => proxyApi.start(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proxy', 'status'] }),
  })
}

export function useProxyStop() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => proxyApi.stop(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proxy', 'status'] }),
  })
}

export function useInstallCert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => proxyApi.installCert(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proxy', 'status'] }),
  })
}
