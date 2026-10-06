import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBlock, makeOccurrences, makeRequest } from '../../test/fixtures'
import { RequestWorkbench } from './RequestWorkbench'
import type { ShowMode, WorkbenchDirection } from './RequestWorkbench'
import type { Request } from '../../api/client'

const blocks = [
  makeBlock({ id: 1, position: 0, block_type: 'system_prompt', token_count: 20, content: 'system rules' }),
  makeBlock({ id: 2, position: 1, block_type: 'user_message', token_count: 10, content: 'find this phrase' }),
  makeBlock({ id: 4, position: 2, block_type: 'tool_definition', tool_name: 'empty_tool', token_count: 0, content: '{}' }),
  makeBlock({ id: 3, direction: 'output', position: 0, block_type: 'assistant_message', token_count: 5, content: 'answer' }),
]
const tokenTotals = { input: { system: 20, user: 10, tool_definition: 0 }, output: { assistant: 5 } }

interface HarnessSelection { initialBlockId?: number | null; onBlockSelect?: (id: number | null) => void }

function Harness({ request, parentRequestId, initialBlockId, onBlockSelect }: { request: Request; parentRequestId?: string | null } & HarnessSelection) {
  const [direction, setDirection] = useState<WorkbenchDirection>('input')
  const [showMode, setShowMode] = useState<ShowMode>('all')
  return <RequestWorkbench request={request} activeDirection={direction} onDirectionChange={setDirection} parentRequestId={parentRequestId} showMode={showMode} onShowModeChange={setShowMode} initialBlockId={initialBlockId} onBlockSelect={onBlockSelect} />
}

function renderWorkbench(request = makeRequest(), parentRequestId: string | null = null, selection: HarnessSelection = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Harness request={request} parentRequestId={parentRequestId} {...selection} /></QueryClientProvider>)
}

function mockBlocksAndDiff(newIds: number[]) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/occurrences')) return new Response(JSON.stringify(makeOccurrences()), { status: 200 })
    if (url.includes('/context-diff')) return new Response(JSON.stringify({ parent_request_id: 'parent', child_request_id: 'request-1', delta: {}, new_child_block_ids: newIds }), { status: 200 })
    return new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 })
  })
}

afterEach(() => vi.restoreAllMocks())

describe('RequestWorkbench', () => {
  it('opens on Request, switches direction and preserves selection', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 }))
    renderWorkbench()
    const input = await screen.findByRole('button', { name: /User.*position 2/i })
    expect(screen.getByRole('button', { name: 'System20' }).getAttribute('title')).toBe('System: 20 tokens')
    expect(screen.getByRole('button', { name: 'User10' }).getAttribute('title')).toBe('User: 10 tokens')
    await userEvent.click(input)
    expect(input.getAttribute('aria-pressed')).toBe('true')
    const contentViewer = screen.getByRole('region', { name: /User content/i })
    expect(screen.getByRole('searchbox', { name: /Search user content/i })).toBeTruthy()
    const primaryPane = contentViewer.closest('[data-workbench-primary-pane]')
    expect(primaryPane?.contains(screen.getByRole('group', { name: /block map/i }))).toBe(true)
    expect(primaryPane?.contains(screen.getByRole('complementary', { name: /Block inspector/i }))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: /Response/i }))
    expect(await screen.findByRole('button', { name: /Assistant.*5 tokens/i })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /Request/i }))
    expect((await screen.findByRole('button', { name: /User.*position 2/i })).getAttribute('aria-pressed')).toBe('true')
  })

  it('filters blocks and exposes Raw in the same workbench', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 }))
    renderWorkbench()
    await screen.findByRole('button', { name: /User.*position 2/i })
    const hideZero = screen.getByRole('checkbox', { name: /Hide zero-token/i }) as HTMLInputElement
    expect(hideZero.checked).toBe(false)
    const zeroBlock = screen.getByRole('button', { name: /Tool definition: empty_tool, 0 tokens/i })
    expect(zeroBlock.style.backgroundColor).toContain('var(--zero-token-neutral)')
    await userEvent.click(hideZero)
    expect(screen.queryByRole('button', { name: /Tool definition: empty_tool, 0 tokens/i })).toBeNull()
    await userEvent.type(screen.getByRole('searchbox', { name: /Search request blocks/i }), 'system rules')
    await waitFor(() => expect(screen.queryByRole('button', { name: /User.*position 2/i })).toBeNull())
    await userEvent.click(screen.getByRole('button', { name: 'Raw' }))
    expect(screen.getByRole('region', { name: 'Structured JSON' })).toBeTruthy()
    expect(screen.getByText('"prompt"')).toBeTruthy()
    expect(screen.getByText('"hello"')).toBeTruthy()
    const rawSearch = screen.getByRole('searchbox', { name: /Search raw request payload/i })
    await userEvent.type(rawSearch, 'hello')
    expect(screen.getByText('1 / 1')).toBeTruthy()
    expect((screen.getByRole('combobox', { name: 'Formatting' }) as HTMLSelectElement).value).toBe('structured')
  })

  it('shows canonical and original wire JSON separately for reconstructed threads', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 }))
    renderWorkbench(makeRequest({
      request_body: '{"messages":[{"role":"user","content":"earlier"}]}',
      canonical_request_body: '{"messages":[{"role":"user","content":"earlier"}]}',
      raw_request_body: '{"thread":{"type":"continue","previous_message_id":"msg_1"},"messages":[{"role":"user","content":"next"}]}',
    }))
    await userEvent.click(screen.getByRole('button', { name: 'Raw' }))
    expect(screen.getByRole('searchbox', { name: /Search canonical request payload/i })).toBeTruthy()
    expect(screen.getByText('"earlier"')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Wire' }))
    expect(screen.getByRole('button', { name: 'Wire' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('searchbox', { name: /Search original wire request/i })).toBeTruthy()
    expect(screen.getByText('"previous_message_id"')).toBeTruthy()
    expect(screen.queryByText('"earlier"')).toBeNull()
  })

  it('changes block size with the size selector', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 }))
    renderWorkbench()
    await screen.findByRole('button', { name: /User.*position 2/i })
    await userEvent.click(screen.getByRole('button', { name: 'Compact' }))

    const map = await screen.findByRole('group', { name: 'Compact block map' })
    const size = screen.getByRole('combobox', { name: 'Size' }) as HTMLSelectElement

    expect(size.value).toBe('26')
    expect(Array.from(size.options, (option) => option.text)).toEqual(['Smaller', 'Default', 'Larger'])
    expect(map.style.gridTemplateColumns).toContain('26px')

    await userEvent.selectOptions(size, '30')

    expect(size.value).toBe('30')
    expect(map.style.gridTemplateColumns).toContain('30px')
  })

  it('clears selection when its block is filtered out', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks, token_totals: tokenTotals }), { status: 200 }))
    renderWorkbench()
    await userEvent.click(await screen.findByRole('button', { name: /User.*position 2/i }))
    expect(screen.getByRole('region', { name: /User content/i })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'User10' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: /User content/i })).toBeNull())
    expect(screen.getByText(/Select a block to inspect/i)).toBeTruthy()
  })

  it('orders request blocks by size and disables grouping for the response', async () => {
    const differentlySizedBlocks = blocks.map((block) => block.id === 2 ? { ...block, token_count: 30 } : block)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks: differentlySizedBlocks }), { status: 200 }))
    renderWorkbench()
    await screen.findByRole('button', { name: /User.*position 2/i })
    await userEvent.click(screen.getByRole('button', { name: 'Compact' }))

    const map = await screen.findByRole('group', { name: 'Compact block map' })
    const arrangement = screen.getByRole('combobox', { name: 'Arrange' }) as HTMLSelectElement
    expect(Array.from(arrangement.options, (option) => option.text)).toEqual([
      'Sequence', 'Turn', 'Tool pair', 'Size (largest first)',
    ])

    await userEvent.selectOptions(arrangement, 'largestFirst')
    expect([...map.querySelectorAll<HTMLElement>('[data-block-map-item]')].map((item) => item.dataset.blockId)).toEqual(['2', '1', '4'])

    await userEvent.click(screen.getByRole('button', { name: /Response/i }))
    expect(arrangement.disabled).toBe(true)
    expect(arrangement.value).toBe('sequence')

    await userEvent.click(screen.getByRole('button', { name: /Request/i }))
    expect(arrangement.disabled).toBe(false)
    expect(arrangement.value).toBe('largestFirst')
  })

  describe('Show control', () => {
    it('sits after Size with All / New only / Highlight new and is disabled without a parent', async () => {
      mockBlocksAndDiff([2])
      renderWorkbench()
      await screen.findByRole('button', { name: /User.*position 2/i })
      const show = screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement
      expect(Array.from(show.options, (option) => option.text)).toEqual(['All', 'New only', 'Highlight new'])
      expect(show.disabled).toBe(true)
      expect(show.title).toMatch(/No previous request/)
      const size = screen.getByRole('combobox', { name: 'Size' })
      expect(size.compareDocumentPosition(show) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it('New only keeps only blocks the backend flagged as new and composes with other filters', async () => {
      mockBlocksAndDiff([2, 4])
      renderWorkbench(makeRequest(), 'parent')
      await screen.findByRole('button', { name: /User.*position 2/i })
      const show = await waitFor(() => {
        const element = screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement
        expect(element.disabled).toBe(false)
        return element
      })
      await userEvent.selectOptions(show, 'new')
      expect(screen.getByRole('button', { name: /User.*position 2/i })).toBeTruthy()
      expect(screen.queryByRole('button', { name: /System.*position 1/i })).toBeNull()
      await userEvent.click(screen.getByRole('checkbox', { name: /Hide zero-token/i }))
      expect(screen.queryByRole('button', { name: /Tool definition: empty_tool/i })).toBeNull()
      expect(screen.getByRole('button', { name: /User.*position 2/i })).toBeTruthy()
    })

    it('Highlight new dims blocks already in the previous request but not new or selected ones', async () => {
      mockBlocksAndDiff([2])
      renderWorkbench(makeRequest(), 'parent')
      const show = await waitFor(() => {
        const element = screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement
        expect(element.disabled).toBe(false)
        return element
      })
      await userEvent.selectOptions(show, 'highlight')
      const system = screen.getByRole('button', { name: /System.*already in previous request/i })
      expect(system.className).toContain('opacity-50')
      const user = screen.getByRole('button', { name: /User.*position 2/i })
      expect(user.className).not.toContain('opacity-50')
      await userEvent.click(system)
      expect(system.className).not.toContain('opacity-50')
    })

    it('is disabled on the Response tab', async () => {
      mockBlocksAndDiff([2])
      renderWorkbench(makeRequest(), 'parent')
      await screen.findByRole('button', { name: /User.*position 2/i })
      await userEvent.click(screen.getByRole('button', { name: /Response/i }))
      expect((screen.getByRole('combobox', { name: 'Show' }) as HTMLSelectElement).disabled).toBe(true)
    })
  })

  describe('block selection from the page', () => {
    it('selects the block named by initialBlockId once the blocks have loaded', async () => {
      mockBlocksAndDiff([])
      const onBlockSelect = vi.fn()
      renderWorkbench(makeRequest(), null, { initialBlockId: 2, onBlockSelect })
      const selected = await screen.findByRole('button', { name: /User.*position 2/i })
      await waitFor(() => expect(selected.getAttribute('aria-pressed')).toBe('true'))
      expect(onBlockSelect).toHaveBeenCalledWith(2)
      expect(screen.getByRole('complementary', { name: /Block inspector/i })).toBeTruthy()
    })

    it('switches to the response when the initial block is an output block', async () => {
      mockBlocksAndDiff([])
      renderWorkbench(makeRequest(), null, { initialBlockId: 3 })
      expect((await screen.findByRole('button', { name: /Assistant.*5 tokens/i })).getAttribute('aria-pressed')).toBe('true')
    })

    it('ignores an id that is not among the request blocks', async () => {
      mockBlocksAndDiff([])
      const onBlockSelect = vi.fn()
      renderWorkbench(makeRequest(), null, { initialBlockId: 999, onBlockSelect })
      await screen.findByRole('button', { name: /User.*position 2/i })
      expect(onBlockSelect).not.toHaveBeenCalled()
      expect(screen.getByText(/Select a block to inspect/i)).toBeTruthy()
    })

    it('reports selection changes so the page can mirror them', async () => {
      mockBlocksAndDiff([])
      const onBlockSelect = vi.fn()
      renderWorkbench(makeRequest(), null, { onBlockSelect })
      await userEvent.click(await screen.findByRole('button', { name: /User.*position 2/i }))
      expect(onBlockSelect).toHaveBeenLastCalledWith(2)
      await userEvent.click(screen.getByRole('button', { name: 'Close block inspector' }))
      expect(onBlockSelect).toHaveBeenLastCalledWith(null)
    })
  })
})

