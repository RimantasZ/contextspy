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
import { useCreateSession, useRenameSession } from './hooks'

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>()
  return {
    ...actual,
    sessionsApi: {
      ...actual.sessionsApi,
      create: vi.fn().mockResolvedValue({}),
      rename: vi.fn().mockResolvedValue({}),
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
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey)
    expect(keys).toContainEqual(['sessions'])
    expect(keys).toContainEqual(['stats'])
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
