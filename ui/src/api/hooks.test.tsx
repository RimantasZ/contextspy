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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { sessionsApi } from './client'
import type { LineageGraph } from './client'
import { useArchiveSession, useCreateSession, useRenameSession, useSessionLineage } from './hooks'

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>()
  return {
    ...actual,
    sessionsApi: {
      ...actual.sessionsApi,
      create: vi.fn().mockResolvedValue({}),
      archive: vi.fn().mockResolvedValue({}),
      rename: vi.fn().mockResolvedValue({}),
      lineageRevision: vi.fn().mockResolvedValue({ revision: 'v1' }),
      lineage: vi.fn().mockResolvedValue({ analysis_version: 'test' }),
    },
  }
})

function setup() {
  const qc = new QueryClient()
  const spy = vi.spyOn(qc, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  return { spy, wrapper }
}

describe('session mutation invalidation', () => {
  it('useCreateSession invalidates sessions and stats', async () => {
    const { spy, wrapper } = setup()
    const { result } = renderHook(() => useCreateSession(), { wrapper })
    result.current.mutate('x')
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(3))
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey)
    expect(keys).toContainEqual(['sessions'])
    expect(keys).toContainEqual(['stats'])
    expect(keys).toContainEqual(['session-conversations'])
  })

  it('useArchiveSession refreshes sessions, stats, conversations and every request view', async () => {
    const { spy, wrapper } = setup()
    const { result } = renderHook(() => useArchiveSession(), { wrapper })
    result.current.mutate('s1')
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(5))
    expect(sessionsApi.archive).toHaveBeenCalledWith('s1')
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey)
    for (const key of ['sessions', 'stats', 'session-conversations', 'request', 'requests']) expect(keys).toContainEqual([key])
  })

  it('useRenameSession invalidates the dashboard-live query', async () => {
    const { spy, wrapper } = setup()
    const { result } = renderHook(() => useRenameSession(), { wrapper })
    result.current.mutate({ id: 's1', name: 'new' })
    await waitFor(() => expect(spy).toHaveBeenCalled())
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey)
    expect(keys).toContainEqual(['stats', 'dashboard-live'])
    expect(keys).toContainEqual(['session', 's1'])
  })
})

describe('revision-bound diagnostics loading', () => {
  it('reuses the full graph until its lightweight revision changes', async () => {
    const qc = new QueryClient()
    const ownWrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    vi.mocked(sessionsApi.lineageRevision).mockResolvedValue({ revision: 'v1' })
    vi.mocked(sessionsApi.lineage).mockResolvedValue({ analysis_version: 'test' } as LineageGraph)
    vi.mocked(sessionsApi.lineage).mockClear()
    const { result } = renderHook(() => useSessionLineage('s1'), { wrapper: ownWrapper })
    await waitFor(() => expect(result.current.data?.analysis_version).toBe('test'))
    expect(sessionsApi.lineage).toHaveBeenCalledTimes(1)
    await qc.invalidateQueries({ queryKey: ['lineage-revision', 's1'] })
    expect(sessionsApi.lineage).toHaveBeenCalledTimes(1)
    vi.mocked(sessionsApi.lineageRevision).mockResolvedValue({ revision: 'v2' })
    await qc.invalidateQueries({ queryKey: ['lineage-revision', 's1'] })
    await waitFor(() => expect(sessionsApi.lineage).toHaveBeenCalledTimes(2))
  })
})
