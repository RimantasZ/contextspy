import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BlockOccurrences as Occurrences } from '../../api/client'
import { makeOccurrences } from '../../test/fixtures'
import { BlockOccurrences } from './BlockOccurrences'

function renderSection(onOpen?: (entry: unknown) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BlockOccurrences requestId="request-1" blockId={1} onOpen={onOpen} /></QueryClientProvider>)
}

function mockApi(handler: (url: string) => unknown | Response) {
  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const result = handler(String(input))
    return result instanceof Response ? result : new Response(JSON.stringify(result), { status: 200 })
  })
  return spy
}

afterEach(() => vi.restoreAllMocks())

describe('BlockOccurrences', () => {
  it('summarises where the block occurs using the API figures', async () => {
    mockApi(() => makeOccurrences())
    renderSection()
    expect(await screen.findByText(/of 5 requests/)).toBeTruthy()
    expect(screen.getByText('3', { selector: 'strong' })).toBeTruthy()
    expect(screen.getByText(/10 tokens each/)).toBeTruthy()
    expect(screen.getByText('30 visible tokens total')).toBeTruthy()
    expect(screen.getByText(/First #12 · last #14 · still in the latest request/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /#12–#14/ })).toBeTruthy()
  })

  it('requests the conversation scope first and refetches for the session without dropping the data', async () => {
    const spy = mockApi((url) => makeOccurrences({ scope: url.includes('scope=session') ? 'session' : 'conversation' }))
    renderSection()
    await screen.findByText(/of 5 requests/)
    expect(String(spy.mock.calls[0][0])).toContain('/requests/request-1/blocks/1/occurrences?scope=conversation')
    expect(screen.getByRole('button', { name: 'Conversation' }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(screen.getByRole('button', { name: 'Session' }))
    // The previous figures stay visible while the new scope loads.
    expect(screen.getByText(/of 5 requests/)).toBeTruthy()
    await waitFor(() => expect(spy.mock.calls.some(([url]) => String(url).includes('scope=session'))).toBe(true))
    expect(screen.getByRole('button', { name: 'Session' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('mentions repeated occurrences and partial or opaque requests', async () => {
    mockApi(() => makeOccurrences({
      totals: { ...makeOccurrences().totals, occurrence_count: 7, fidelity_counts: { complete: 1, partial: 1, opaque: 1 } },
    }))
    renderSection()
    expect(await screen.findByText(/\(7 occurrences\)/)).toBeTruthy()
    expect(screen.getByText('1 partial · 1 opaque requests')).toBeTruthy()
  })

  it('shows no per-occurrence figure when occurrences disagree', async () => {
    mockApi(() => makeOccurrences({ totals: { ...makeOccurrences().totals, tokens_per_occurrence: null } }))
    renderSection()
    await screen.findByText('30 visible tokens total')
    expect(screen.queryByText(/tokens each/)).toBeNull()
  })

  it('expands a run lazily and opens an occurrence with its own block id', async () => {
    const open = vi.fn()
    const spy = mockApi((url) => url.includes('/occurrences/requests')
      ? { scope: 'conversation', has_more: false, requests: makeOccurrences().requests_sample }
      : makeOccurrences())
    renderSection(open)
    const run = await screen.findByRole('button', { name: /#12–#14/ })
    expect(spy.mock.calls.some(([url]) => String(url).includes('/occurrences/requests'))).toBe(false)
    await userEvent.click(run)
    expect(run.getAttribute('aria-expanded')).toBe('true')
    await userEvent.click(await screen.findByRole('button', { name: 'Open request #12 C1 with this block selected' }))
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ request_id: 'a', block_id: 1 }))
    const requested = String(spy.mock.calls.find(([url]) => String(url).includes('/occurrences/requests'))![0])
    expect(requested).toContain('from_position=0&to_position=2')
    expect(screen.getByText('(this request)')).toBeTruthy()
  })

  it('does not make entries clickable without an opener', async () => {
    mockApi((url) => url.includes('/occurrences/requests')
      ? { scope: 'conversation', has_more: true, requests: makeOccurrences().requests_sample }
      : makeOccurrences())
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /#12–#14/ }))
    const entry = await screen.findByRole('button', { name: /Open request #12/ }) as HTMLButtonElement
    expect(entry.disabled).toBe(true)
    expect(screen.getByText(/Showing the first 2 requests of this run/)).toBeTruthy()
  })

  it.each([
    ['auxiliary_request', 'Auxiliary request: showing the whole session.'],
    ['no_session', 'This request has no session: showing only itself.'],
    ['conversation_unavailable', 'Conversation not available: showing the whole session.'],
  ] as const)('explains a scope fallback (%s)', async (note, caption) => {
    mockApi(() => makeOccurrences({ scope_note: note }))
    renderSection()
    expect(await screen.findByText(caption)).toBeTruthy()
  })

  it('explains blocks that cannot be matched across requests', async () => {
    mockApi(() => makeOccurrences({ identity: { ...makeOccurrences().identity, kind: 'none' } }))
    renderSection()
    expect(await screen.findByText(/no content identity/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /#12/ })).toBeNull()
  })

  it('shows an error instead of crashing when the request fails or the response is malformed', async () => {
    mockApi(() => new Response('{}', { status: 500 }))
    const first = renderSection()
    expect(await screen.findByText('Could not load where this block occurs.')).toBeTruthy()
    first.unmount()
    mockApi(() => ({ session_seq: 1, blocks: [] } as unknown as Occurrences))
    renderSection()
    expect(await screen.findByText('Could not load where this block occurs.')).toBeTruthy()
  })
})
