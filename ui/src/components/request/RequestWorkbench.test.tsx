import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBlock, makeRequest } from '../../test/fixtures'
import { RequestWorkbench } from './RequestWorkbench'
import type { WorkbenchDirection } from './RequestWorkbench'

const blocks = [
  makeBlock({ id: 1, position: 0, block_type: 'system_prompt', token_count: 20, content: 'system rules' }),
  makeBlock({ id: 2, position: 1, block_type: 'user_message', token_count: 10, content: 'find this phrase' }),
  makeBlock({ id: 3, direction: 'output', position: 0, block_type: 'assistant_message', token_count: 5, content: 'answer' }),
]

function Harness() {
  const [direction, setDirection] = useState<WorkbenchDirection>('input')
  return <RequestWorkbench request={makeRequest()} activeDirection={direction} onDirectionChange={setDirection} />
}

function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)
}

afterEach(() => vi.restoreAllMocks())

describe('RequestWorkbench', () => {
  it('opens on Request, switches direction and preserves selection', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks }), { status: 200 }))
    renderWorkbench()
    const input = await screen.findByRole('button', { name: /User.*position 2/i })
    await userEvent.click(input)
    expect(input.getAttribute('aria-pressed')).toBe('true')
    await userEvent.click(screen.getByRole('button', { name: /Response/i }))
    expect(await screen.findByRole('button', { name: /Assistant.*5 tokens/i })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /Request/i }))
    expect((await screen.findByRole('button', { name: /User.*position 2/i })).getAttribute('aria-pressed')).toBe('true')
  })

  it('filters blocks and exposes Raw in the same workbench', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks }), { status: 200 }))
    renderWorkbench()
    await screen.findByRole('button', { name: /User.*position 2/i })
    await userEvent.type(screen.getByRole('searchbox', { name: /Search request blocks/i }), 'system rules')
    await waitFor(() => expect(screen.queryByRole('button', { name: /User.*position 2/i })).toBeNull())
    await userEvent.click(screen.getByRole('button', { name: 'Raw' }))
    expect(screen.getByText(/"prompt": "hello"/)).toBeTruthy()
  })
})
