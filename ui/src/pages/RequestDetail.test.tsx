import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBlock, makeOccurrences, makeRequest } from '../test/fixtures'
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
  c: [makeBlock({ id: 21, position: 0, block_type: 'user_message', content: 'side request' })],
}

function mockApi(requestOverrides: Partial<ReturnType<typeof makeRequest>> = {}) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })
    if (url.includes('/context-diff')) return json({ parent_request_id: 'a', child_request_id: 'b', delta: {}, new_child_block_ids: [12] })
    if (url.includes('/occurrences/requests')) return json({ scope: 'conversation', requests: [makeOccurrences().requests_sample[0]], has_more: false })
    if (url.includes('/occurrences')) return json(makeOccurrences({ requests_sample: [
      { request_id: 'a', block_id: 1, position: 0, session_seq: 33, conversation_code: 'C1', token_count: 10, context_fidelity: 'complete', is_current: false },
      { request_id: 'b', block_id: 12, position: 1, session_seq: 34, conversation_code: 'C1', token_count: 10, context_fidelity: 'complete', is_current: true },
    ] }))
    const blocks = /\/requests\/(a|b|c|e|f)\/blocks/.exec(url)
    if (blocks) return json({ session_seq: 1, blocks: (blocksFor[blocks[1]] ?? blocksFor.c), token_totals: {} })
    if (url.includes('/tools')) return json({ tools: [] })
    if (url.includes('/lineage/revision')) return json({ revision: '1' })
    if (url.includes('/lineage')) return json({ nodes: [{ request_id: 'a', session_seq: 33, conversation_code: 'C1', external: false }, { request_id: 'b', session_seq: 34, conversation_code: 'AUX', external: false }, { request_id: 'c', session_seq: 35, conversation_code: 'C1', external: false, conversation_previous_request_id: 'b', conversation_next_request_id: 'd' }, { request_id: 'd', session_seq: 36, conversation_code: 'C1', external: false },
      { request_id: 'e', session_seq: 37, conversation_code: 'C1', external: false, conversation_previous_request_id: 'b', conversation_next_request_id: 'f' },
      { request_id: 'f', session_seq: 38, conversation_code: 'C1', external: false, conversation_previous_request_id: 'e', conversation_next_request_id: 'g' }], edges: [{ source_request_id: 'a', target_request_id: 'b', relation_type: 'context_continuation', certainty: 'exact', confidence: 1 },
      { source_request_id: 'a', target_request_id: 'e', relation_type: 'context_continuation', certainty: 'exact', confidence: 1 },
      { source_request_id: 'e', target_request_id: 'f', relation_type: 'context_continuation', certainty: 'exact', confidence: 1 },
      { source_request_id: 'e', target_request_id: 'g', relation_type: 'context_continuation', certainty: 'exact', confidence: 1 }] })
    const request = /\/requests\/(a|b|c|e|f)$/.exec(url)
    if (request) return json({ request: makeRequest({ id: request[1], session_id: 's1', ...requestOverrides }) })
    return new Response('{}', { status: 404 })
  })
}

function renderPage(path = '/requests/b') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
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

  it('falls back to the previous/next request in the conversation when no direct link exists', async () => {
    mockApi()
    renderPage('/requests/c')
    expect(await screen.findByRole('heading', { name: 'Request #C1-35' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^← Parent/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Next in conversation #C1-36/ })).toBeTruthy()
    // The Show baseline is the fallback request and the control says so.
    const show = await showSelect()
    expect(show.title).toMatch(/previous request in this conversation/)
    await userEvent.click(screen.getByRole('button', { name: /Previous in conversation #AUX-34/ }))
    expect(await screen.findByRole('heading', { name: 'Request #AUX-34' })).toBeTruthy()
  })

  it('marks fallback neighbours with a warning and explains them in a keyboard-reachable tooltip', async () => {
    mockApi()
    renderPage('/requests/c')
    const previous = await screen.findByRole('button', { name: /Previous in conversation #AUX-34/ })
    expect(previous.textContent).toContain('⚠')
    expect(previous.getAttribute('aria-describedby')).toBe('neighbour-tip-previous')
    expect(document.getElementById('neighbour-tip-previous')?.textContent).toMatch(/No direct parent was established.*may not be the request this one actually continued/)
    expect(screen.getByRole('button', { name: /Next in conversation #C1-36/ }).textContent).toContain('⚠')
  })

  it('shows Previous/Next beside Parent/Child when they are different requests, without a warning', async () => {
    mockApi()
    renderPage('/requests/e')
    // e's parent is a (#C1-33) but the adjacent request is b (#AUX-34); its children are f and g, and the adjacent next is f.
    expect(await screen.findByRole('button', { name: /Parent #C1-33/ })).toBeTruthy()
    const previous = screen.getByRole('button', { name: /Previous in conversation #AUX-34/ })
    expect(previous.textContent).not.toContain('⚠')
    expect(document.getElementById('neighbour-tip-previous')?.textContent).toBe('The previous request in the same conversation.')
    expect(screen.getByRole('button', { name: /Child #C1-38/ })).toBeTruthy()
    // The adjacent next request (f) is already a child, so it is not duplicated.
    expect(screen.queryByRole('button', { name: /Next in conversation/ })).toBeNull()
  })

  it('does not repeat the parent as Previous when they are the same request', async () => {
    mockApi()
    renderPage('/requests/f')
    expect(await screen.findByRole('button', { name: /Parent #C1-37/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Previous in conversation/ })).toBeNull()
  })
})

describe('RequestDetail block selection in the URL', () => {
  function WithLocation() {
    const location = useLocation()
    return <output data-testid="location">{location.pathname + location.search}</output>
  }

  function renderWithLocation(path: string) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/requests/:id" element={<RequestDetail />} /></Routes>
          <WithLocation />
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  it('selects the block named by ?block= and keeps the parameter', async () => {
    mockApi()
    renderWithLocation('/requests/b?block=12')
    const selected = await screen.findByRole('button', { name: /User.*position 2/i })
    await waitFor(() => expect(selected.getAttribute('aria-pressed')).toBe('true'))
    expect(screen.getByTestId('location').textContent).toBe('/requests/b?block=12')
  })

  it('mirrors selection changes in the URL', async () => {
    mockApi()
    renderWithLocation('/requests/b')
    await userEvent.click(await screen.findByRole('button', { name: /User.*position 2/i }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/requests/b?block=12'))
    await userEvent.click(screen.getByRole('button', { name: 'Close block inspector' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/requests/b'))
  })

  it('ignores a malformed ?block= value', async () => {
    mockApi()
    renderWithLocation('/requests/b?block=abc')
    await screen.findByRole('button', { name: /User.*position 2/i })
    expect(screen.getByText(/Select a block to inspect/i)).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/requests/b?block=abc')
  })

  it('opens an occurrence in its own request with its block selected', async () => {
    mockApi()
    renderWithLocation('/requests/b?block=12')
    const run = await screen.findByRole('button', { name: /#12–#14/ })
    await userEvent.click(run)
    await userEvent.click(await screen.findByRole('button', { name: /Open request #12 C1 with this block selected/ }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/requests/a?block=1'))
    const selected = await screen.findByRole('button', { name: /System.*position 1/i })
    await waitFor(() => expect(selected.getAttribute('aria-pressed')).toBe('true'))
  })
})

describe('RequestDetail content state', () => {
  it('explains that an archived session no longer stores payloads', async () => {
    mockApi({ content_state: 'archived', session_archived_at: '2026-10-05T10:00:00' })
    renderPage('/requests/b')
    expect(await screen.findByText('Archived session')).toBeTruthy()
  })

  it('shows no notice while payloads are stored', async () => {
    mockApi({ content_state: 'retained' })
    renderPage('/requests/b')
    await screen.findByRole('button', { name: /User.*position 2/i })
    expect(screen.queryByText('Archived session')).toBeNull()
    expect(screen.queryByText(/no longer stored/)).toBeNull()
  })
})
