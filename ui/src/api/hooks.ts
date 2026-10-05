// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { sessionsApi, requestsApi, statsApi, proxyApi } from './client'
import type { OccurrenceScope } from './client'

// ---- Sessions -------------------------------------------------------------

export function useSessions() {
  return useQuery({
    queryKey: ['sessions'],
    queryFn: () => sessionsApi.list(),
    refetchInterval: 10_000,
  })
}

export function useDashboardLive() {
  return useQuery({
    queryKey: ['stats', 'dashboard-live'],
    queryFn: () => statsApi.dashboardLive(),
    refetchInterval: 5_000,
  })
}

export function useSession(id: string) {
  return useQuery({
    queryKey: ['session', id],
    queryFn: () => sessionsApi.get(id),
    enabled: !!id,
  })
}

export function useSessionLineage(id: string, enabled: boolean = true) {
  const revision = useQuery({
    queryKey: ['lineage-revision', id],
    queryFn: () => sessionsApi.lineageRevision(id),
    enabled: !!id && enabled,
    refetchInterval: 5_000,
  })
  const graph = useQuery({
    queryKey: ['lineage', id, revision.data?.revision],
    queryFn: () => sessionsApi.lineage(id),
    enabled: !!id && enabled && !!revision.data?.revision,
    staleTime: Infinity,
  })
  return { ...graph, isLoading: revision.isLoading || graph.isLoading,
    error: revision.error ?? graph.error }
}

export function useSessionConversations(id: string, enabled: boolean = true) {
  return useQuery({
    queryKey: ['session-conversations', id],
    queryFn: () => sessionsApi.conversations(id),
    enabled: !!id && enabled,
    refetchInterval: 5_000,
  })
}

export function useSessionSequence(id: string, enabled: boolean = true) {
  return useQuery({
    queryKey: ['session-sequence', id],
    queryFn: () => sessionsApi.sequence(id),
    enabled: !!id && enabled,
    refetchInterval: 5_000,
  })
}

export function useRequestContext(sessionId: string, requestId: string | null, revision?: string) {
  return useQuery({
    queryKey: ['request-context', sessionId, requestId, revision],
    queryFn: () => sessionsApi.requestContext(sessionId, requestId!, revision),
    enabled: !!sessionId && !!requestId,
  })
}

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => sessionsApi.create(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['session-conversations'] })
    },
  })
}

export function useEndSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => sessionsApi.end(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['session-conversations'] })
    },
  })
}

export function useRenameSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => sessionsApi.rename(id, name),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['session', id] })
      qc.invalidateQueries({ queryKey: ['stats', 'dashboard-live'] })
      qc.invalidateQueries({ queryKey: ['session-conversations', id] })
    },
  })
}

export function useDeleteSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, deleteRequests = false }: { id: string; deleteRequests?: boolean }) =>
      sessionsApi.delete(id, deleteRequests),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      qc.invalidateQueries({ queryKey: ['session-conversations'] })
    },
  })
}

// ---- Requests -------------------------------------------------------------

export function useRequests(params: { session_id?: string; provider?: string; agent?: string; model?: string; q?: string; status_category?: string; sort_by?: string; sort_dir?: string; limit?: number; offset?: number }) {
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

export function useRequestBlocks(id: string, enabled: boolean = true) {
  return useQuery({
    queryKey: ['request', id, 'blocks'],
    queryFn: () => requestsApi.blocks(id),
    enabled: !!id && enabled,
  })
}

/** Where one block's content occurs across its conversation/session; kept while only the scope changes. */
export function useBlockOccurrences(requestId: string, blockId: number | null, scope: OccurrenceScope) {
  return useQuery({
    queryKey: ['request', requestId, 'block', blockId, 'occurrences', scope],
    queryFn: () => requestsApi.blockOccurrences(requestId, blockId ?? 0, scope),
    enabled: !!requestId && blockId != null,
    staleTime: 60_000,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === requestId && previousQuery?.queryKey[3] === blockId ? previous : undefined,
  })
}

/** The occurring requests of one run, fetched when the run is expanded. */
export function useOccurrenceRequests(requestId: string, blockId: number, scope: OccurrenceScope, run: { from: number; to: number } | null) {
  return useQuery({
    queryKey: ['request', requestId, 'block', blockId, 'occurrences', scope, run?.from, run?.to],
    queryFn: () => requestsApi.occurrenceRequests(requestId, blockId, scope, run?.from ?? 0, run?.to ?? 0),
    enabled: run != null,
    staleTime: 60_000,
  })
}

export function useContextDiff(childId: string, parentId: string | null) {
  return useQuery({
    queryKey: ['request', childId, 'context-diff', parentId],
    queryFn: () => requestsApi.contextDiff(childId, parentId ?? ''),
    enabled: !!childId && !!parentId,
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

export function useToolStats(sessionId?: string) {
  return useQuery({
    queryKey: ['stats', 'tools', sessionId],
    queryFn: () => statsApi.tools(sessionId),
    refetchInterval: 10_000,
  })
}

export function useSessionsSummary() {
  return useQuery({
    queryKey: ['stats', 'sessions-summary'],
    queryFn: () => statsApi.sessionsSummary(),
    refetchInterval: 10_000,
  })
}

export function useRequestToolStats(requestId: string) {
  return useQuery({
    queryKey: ['stats', 'tools', 'request', requestId],
    queryFn: () => statsApi.tools(undefined, requestId),
    enabled: !!requestId,
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

export function useSetCapturePaused() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (paused: boolean) => (paused ? proxyApi.pause() : proxyApi.resume()),
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
