import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBlock, makeRequest } from '../test/fixtures'
import RequestDetail from './RequestDetail'

vi.mock('../components/TokenDonut', () => ({ TokenDonut: () => null }))
vi.mock('../components/ToolBreakdown', () => ({ ToolBreakdownSection: () => null }))

// Request "b" continues "a". Block 12 is the only block of "b" not present in "a".
const blocksFor: Record<string, ReturnType<typeof makeBlock>[]> = {
  a: [makeBlock({ id: 1, position: 0, block_type: 'system_prompt', content: 'rules' })],
  b: [
    makeBlock({ id: 11, position: 0, block_type: 'system_prompt', content: 'rules' }),
    makeBlock({ id: 12, position: 1, block_type: 'user_message', content: 'next question' }),
  ],
}

function mockApi() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })
    if (url.includes('/context-diff')) return json({ parent_request_id: 'a', child_request_id: 'b', delta: {}, new_child_block_ids: [12] })
    const blocks = /\/requests\/(a|b)\/blocks/.exec(url)
    if (blocks) return json({ session_seq: 1, blocks: blocksFor[blocks[1]], token_totals: {} })
    if (url.includes('/tools')) return json({ tools: [] })
    if (url.includes('/lineage/revision')) return json({ revision: '1' })
    if (url.includes('/lineage')) return json({ nodes: [{ request_id: 'a', session_seq: 33, conversation_code: 'C1', external: false }, { request_id: 'b', session_seq: 34, conversation_code: 'AUX', external: false }], edges: [{ source_request_id: 'a', target_request_id: 'b', relation_type: 'context_continuation', certainty: 'exact', confidence: 1 }] })
    const request = /\/requests\/(a|b)$/.exec(url)
    if (request) return json({ request: makeRequest({ id: request[1], session_id: 's1' }) })
    return new Response('{}', { status: 404 })
  })
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/requests/b']}>
        <Routes>
          <Route path="/requests/:id" element={<RequestDetail />} />
          <Route path="*" element={<Link to="/requests/b">Back to request</Link>} />
        </Routes>
        <Link to="/sessions">Leave</Link>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function showSelect() {
  return waitFor(() => {
    const element = screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement
    expect(element.disabled).toBe(false)
    return element
  })
}

afterEach(() => vi.restoreAllMocks())

describe('RequestDetail Show mode lifetime', () => {
  it('persists across parent/child navigation and resets after leaving the page', async () => {
    mockApi()
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Request #AUX-34' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Parent #C1-33/ })).toBeTruthy()
    await userEvent.selectOptions(await showSelect(), 'new')
    expect(screen.queryByRole('button', { name: /System.*position 1/i })).toBeNull()

    // Parent has no parent of its own: control is disabled but the choice is retained.
    await userEvent.click(await screen.findByRole('button', { name: /Parent #C1-33/ }))
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement).disabled).toBe(true))

    await userEvent.click(await screen.findByRole('button', { name: /Child #AUX-34/ }))
    expect(((await showSelect()) as HTMLSelectElement).value).toBe('new')
    expect(screen.queryByRole('button', { name: /System.*position 1/i })).toBeNull()

    await userEvent.click(screen.getByRole('link', { name: 'Leave' }))
    await userEvent.click(await screen.findByRole('link', { name: 'Back to request' }))
    expect(((await showSelect()) as HTMLSelectElement).value).toBe('all')
  })
})
