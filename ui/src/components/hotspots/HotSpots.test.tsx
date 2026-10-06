import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BlockHotspotRow, FileHotspotRow, SessionHotspots, SourceHotspotRow } from '../../api/client'
import { HotSpots } from './HotSpots'

function blockRow(overrides: Partial<BlockHotspotRow> = {}): BlockHotspotRow {
  return {
    key: 'h1', occurrence_count: 54, request_count: 54, total_tokens: 648000, share_pct: 12.1, block_type: 'tool_definition',
    category: 'tool_definitions', tool_name: 'Bash', source_key: 'tool:Bash', activity: 'command', file_path: null,
    label: 'tool:Bash definition', preview: '{"name":"Bash"}', content_purged: false, tokens_per_occurrence: 12000,
    first_seen_session_seq: 1, last_seen_session_seq: 54, in_latest_request: true, run_count: 1,
    latest: { request_id: 'r54', block_id: 900, session_seq: 54 }, ...overrides,
  }
}

function response(group: 'block' | 'source' | 'file', rows: unknown[], overrides: Record<string, unknown> = {}): SessionHotspots {
  return {
    scope: 'conversation', requested_scope: 'conversation', scope_note: null, group, sort: 'total_tokens',
    conversations: [
      { key: 'c-one', code: 'C1', request_count: 54, selected: true },
      { key: 'c-two', code: 'C2', request_count: 7, selected: false },
    ],
    summary: {
      scope_request_count: 54, visible_tokens_total: 5390318, occurrences_total: 1000, fidelity_counts: { complete: 40, partial: 4, opaque: 10 },
      unidentifiable: group === 'block' ? { blocks: 133, tokens: 432 } : null, returned_tokens: 648000, returned_share_pct: 12.1,
    },
    rows, total_rows: rows.length, has_more: false, ...overrides,
  } as SessionHotspots
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname + location.search}</output>
}

function renderView(initial = '/sessions/s1?view=hotspots') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/sessions/:id" element={<><Location /><HotSpots sessionId="s1" /></>} />
          <Route path="/requests/:id" element={<Location />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockApi(handler: (url: URL) => SessionHotspots | Response) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const result = handler(new URL(String(input), 'http://localhost'))
    return result instanceof Response ? result : new Response(JSON.stringify(result), { status: 200 })
  })
}

afterEach(() => vi.restoreAllMocks())

describe('HotSpots', () => {
  it('lists blocks with totals, badges and the coverage sentence', async () => {
    const dropped = blockRow({ key: 'h2', label: 'tool:Read result · a.py', block_type: 'tool_result', in_latest_request: false, run_count: 2, tokens_per_occurrence: null, total_tokens: 90000, share_pct: 1.7, preview: null, content_purged: true })
    mockApi(() => response('block', [blockRow(), dropped]))
    renderView()
    await screen.findByText('tool:Bash definition')
    expect(screen.getByText(/Top 2 of 2 blocks/).textContent).toContain('12.1%')
    expect(screen.getByText(/5,390,318 visible tokens/)).toBeTruthy()
    expect(screen.getByText('648,000')).toBeTruthy()
    expect(screen.getByText('(12.1%)')).toBeTruthy()
    expect(screen.getByText('dropped')).toBeTruthy()
    expect(screen.queryByText('reappears')).toBeNull()  // detail only
    expect(screen.getByText(/4 partial, 10 opaque/)).toBeTruthy()
    expect(screen.getByText(/133 unidentifiable blocks, 432 tokens/)).toBeTruthy()
    expect(screen.getByRole('option', { name: 'C1 · 54 requests' })).toBeTruthy()
  })

  it('keeps the secondary facts in a row that expands on click', async () => {
    const dropped = blockRow({ key: 'h2', label: 'tool:Read result · a.py', block_type: 'tool_result', in_latest_request: false, run_count: 2, tokens_per_occurrence: null, total_tokens: 90000, share_pct: 1.7, preview: null, content_purged: true })
    mockApi(() => response('block', [blockRow(), dropped]))
    renderView()
    const toggle = await screen.findByRole('button', { name: 'Show details of tool:Bash definition' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await userEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('Occurrences', { selector: 'dt' }).closest('div')?.textContent).toContain('54 (5.4%)')  // 54 of 1,000
    expect(screen.getByText('12,000')).toBeTruthy()
    expect(screen.getByText('{"name":"Bash"}')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Show details of tool:Read result · a.py' }))
    expect(screen.getByText('reappears')).toBeTruthy()
    expect(screen.getByText('sizes differ')).toBeTruthy()
    expect(screen.getByText('(content no longer stored)')).toBeTruthy()
    await userEvent.click(toggle)
    expect(screen.queryByText('{"name":"Bash"}')).toBeNull()
  })

  it('scales bars to the first row and follows the sort metric', async () => {
    const rows = [blockRow({ key: 'a', label: 'big', total_tokens: 1000, occurrence_count: 4 }), blockRow({ key: 'b', label: 'small', total_tokens: 250, occurrence_count: 8 })]
    mockApi(() => response('block', rows))
    renderView()
    await screen.findByText('big')
    expect(screen.getAllByTestId('hotspot-bar').map((bar) => (bar as HTMLElement).style.width)).toEqual(['100%', '25%'])
    expect(screen.getByRole('button', { name: 'Sort by occurrences instead' })).toBeTruthy()
    cleanup()
    mockApi(() => response('block', [rows[1], rows[0]], { sort: 'occurrences' }))
    renderView('/sessions/s1?view=hotspots&sort=occurrences')
    await screen.findByText('small')
    expect(screen.getAllByTestId('hotspot-bar').map((bar) => (bar as HTMLElement).style.width)).toEqual(['100%', '50%'])
    expect(screen.getByText('(0.8%)')).toBeTruthy()  // 8 of 1,000 occurrences
    await userEvent.click(screen.getByRole('button', { name: 'Sort by total tokens instead' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).not.toContain('sort='))
  })

  it('can put the label on the bar (layout=bar)', async () => {
    mockApi(() => response('block', [blockRow({ total_tokens: 1000 }), blockRow({ key: 'tiny', label: 'a very long label for a tiny bar', total_tokens: 1 })]))
    renderView('/sessions/s1?view=hotspots&layout=bar')
    const long = await screen.findByText('a very long label for a tiny bar')
    expect(long.getAttribute('data-placement')).toBe('outside')
    expect(screen.getByText('tool:Bash definition').getAttribute('data-placement')).toBe('inside')
    await userEvent.click(screen.getByRole('button', { name: 'Name column' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).not.toContain('layout='))
  })

  it('sends the default parameters and refetches when the controls change', async () => {
    const spy = mockApi(() => response('block', [blockRow()]))
    renderView()
    await screen.findByText('tool:Bash definition')
    const first = new URL(String(spy.mock.calls[0][0]), 'http://localhost')
    expect(first.pathname).toBe('/api/sessions/s1/hotspots')
    expect(Object.fromEntries(first.searchParams)).toEqual({ group: 'block', sort: 'total_tokens', scope: 'conversation', in_context: 'all', limit: '25', offset: '0' })

    await userEvent.click(screen.getByRole('button', { name: 'Occurrences' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('sort=occurrences'))
    await userEvent.click(screen.getByRole('button', { name: 'Dropped' }))
    await userEvent.selectOptions(screen.getByLabelText('Block type'), 'tool_result')
    await userEvent.selectOptions(screen.getByLabelText('Category'), 'file_contents')
    await waitFor(() => {
      const last = new URL(String(spy.mock.calls[spy.mock.calls.length - 1][0]), 'http://localhost')
      expect(Object.fromEntries(last.searchParams)).toMatchObject({ sort: 'occurrences', in_context: 'dropped', block_type: 'tool_result', category: 'file_contents', offset: '0' })
    })
  })

  it('switches scope to the whole session and to another conversation', async () => {
    const spy = mockApi(() => response('block', [blockRow()]))
    renderView()
    await screen.findByText('tool:Bash definition')
    await userEvent.selectOptions(screen.getByLabelText('Scope'), 'Whole session')
    await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('scope=session'))
    await userEvent.selectOptions(screen.getByLabelText('Scope'), 'c-two')
    await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('conversation=c-two'))
    expect(screen.getByTestId('location').textContent).not.toContain('scope=session')
    await waitFor(() => {
      const last = new URL(String(spy.mock.calls[spy.mock.calls.length - 1][0]), 'http://localhost')
      expect(last.searchParams.get('conversation')).toBe('c-two')
    })
  })

  it('reads a conversation and filters from the URL', async () => {
    const spy = mockApi(() => response('block', [blockRow()]))
    renderView('/sessions/s1?view=hotspots&conversation=c-two&sort=occurrences&in_context=current&source=tool%3ARead&category=tool_results')
    await screen.findByText('tool:Bash definition')
    const url = new URL(String(spy.mock.calls[0][0]), 'http://localhost')
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ conversation: 'c-two', sort: 'occurrences', in_context: 'current', source: 'tool:Read', category: 'tool_results' })
    expect(screen.getByRole('button', { name: 'Remove the source filter tool:Read' })).toBeTruthy()
  })

  it('shows more rows by fetching the next page and stops at the end', async () => {
    const page = (start: number, hasMore: boolean) => response('block', Array.from({ length: 25 }, (_, i) => blockRow({ key: `h${start + i}`, label: `block ${start + i}` })), { total_rows: 40, has_more: hasMore })
    const spy = mockApi((url) => url.searchParams.get('offset') === '0' ? page(0, true) : response('block', Array.from({ length: 15 }, (_, i) => blockRow({ key: `h${25 + i}`, label: `block ${25 + i}` })), { total_rows: 40, has_more: false }))
    renderView()
    await screen.findByText('block 0')
    expect(screen.queryByText('block 25')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
    await screen.findByText('block 25')
    expect(screen.getByText('block 0')).toBeTruthy()
    expect(new URL(String(spy.mock.calls[1][0]), 'http://localhost').searchParams.get('offset')).toBe('25')
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()
    expect(screen.getByText(/Top 40 of 40 blocks/)).toBeTruthy()
  })

  it('opens the latest occurrence with the block selected', async () => {
    mockApi(() => response('block', [blockRow()]))
    renderView()
    await userEvent.click(await screen.findByRole('button', { name: 'Show details of tool:Bash definition' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open the latest request carrying tool:Bash definition' }))
    expect(screen.getByTestId('location').textContent).toBe('/requests/r54?block=900')
  })

  it('groups by source, opens the largest block and filters blocks by source', async () => {
    const source: SourceHotspotRow = {
      key: 'tool:Read', source_key: 'tool:Read', activity: 'read', distinct_blocks: 12, occurrence_count: 80, request_count: 40,
      total_tokens: 500000, share_pct: 9.3, largest: { request_id: 'r9', block_id: 77, session_seq: 9, token_count: 21000, label: 'tool:Read result' },
    }
    const spy = mockApi((url) => response(url.searchParams.get('group') === 'source' ? 'source' : 'block', url.searchParams.get('group') === 'source' ? [source] : [blockRow()]))
    renderView()
    await screen.findByText('tool:Bash definition')
    await userEvent.click(screen.getByRole('button', { name: 'Sources' }))
    await screen.findByText('tool:Read')
    await userEvent.click(screen.getByRole('button', { name: 'Show details of tool:Read' }))
    expect(screen.getByText('Distinct blocks').nextSibling?.textContent).toBe('12')
    expect(screen.queryByLabelText('Block type')).toBeNull()  // block-only filters are hidden
    expect(screen.queryByLabelText('Category')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Show the blocks of tool:Read' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toContain('source=tool%3ARead'))
    expect(screen.getByTestId('location').textContent).not.toContain('group=')
    await waitFor(() => expect(new URL(String(spy.mock.calls[spy.mock.calls.length - 1][0]), 'http://localhost').searchParams.get('source')).toBe('tool:Read'))
  })

  it('groups by file and says how many tokens were read and edited', async () => {
    const file: FileHotspotRow = {
      key: '/p/a.py', file_path: '/p/a.py', distinct_versions: 3, result_tokens: 200, call_tokens: 40, occurrence_count: 5, request_count: 3,
      total_tokens: 240, share_pct: 70.6, first_seen_session_seq: 1, last_seen_session_seq: 3, in_latest_request: false,
      latest: { request_id: 'r3', block_id: 5, session_seq: 3 },
    }
    mockApi(() => response('file', [file]))
    renderView('/sessions/s1?view=hotspots&group=file')
    await screen.findByText('/p/a.py')
    await userEvent.click(screen.getByRole('button', { name: 'Show details of /p/a.py' }))
    expect(screen.getByText('Read').nextSibling?.textContent).toBe('200 tokens')
    expect(screen.getByText('Edited').nextSibling?.textContent).toBe('40 tokens')
    expect(screen.getByText('Versions').nextSibling?.textContent).toBe('3')
    expect(screen.getByText('dropped')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Dropped' })).toBeNull()
  })

  it('explains a scope fallback, and renders archived rows without previews', async () => {
    mockApi(() => response('block', [blockRow({ preview: null, content_purged: true })], { scope: 'session', scope_note: 'auxiliary_request' }))
    renderView()
    await screen.findByText('tool:Bash definition')
    expect(screen.getByText(/auxiliary: showing the whole session/)).toBeTruthy()
    const row = screen.getByRole('button', { name: 'Show details of tool:Bash definition' })
    expect(row.getAttribute('title')).toContain('content no longer stored')
  })

  it('handles loading, empty and failed states', async () => {
    let respond: (response: Response) => void = () => {}
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>((resolve) => { respond = resolve }))
    const { unmount } = renderView()
    expect(screen.getByText('Analysing the context…')).toBeTruthy()
    respond(new Response(JSON.stringify(response('block', [])), { status: 200 }))
    await screen.findByText(/No blocks here/)
    unmount()

    vi.restoreAllMocks()
    mockApi(() => new Response('{"detail":"x"}', { status: 500 }))
    renderView()
    expect((await screen.findByRole('alert')).textContent).toContain('could not be loaded')
  })

  it('offers Show more while the server reports more rows', async () => {
    mockApi(() => response('block', Array.from({ length: 25 }, (_, i) => blockRow({ key: `h${i}`, label: `block ${i}` })), { total_rows: 5000, has_more: true }))
    renderView()
    await screen.findByText('block 0')
    expect(within(screen.getByRole('region', { name: 'Hot spots' })).getByRole('button', { name: 'Show more' })).toBeTruthy()
  })
})
