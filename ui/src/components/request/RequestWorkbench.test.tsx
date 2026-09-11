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
  makeBlock({ id: 4, position: 2, block_type: 'tool_definition', tool_name: 'empty_tool', token_count: 0, content: '{}' }),
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
    expect(screen.getByRole('button', { name: 'System20' }).getAttribute('title')).toBe('System: 20 tokens')
    expect(screen.getByRole('button', { name: 'User10' }).getAttribute('title')).toBe('User: 10 tokens')
    await userEvent.click(input)
    expect(input.getAttribute('aria-pressed')).toBe('true')
    const contentViewer = screen.getByRole('region', { name: /User content/i })
    expect(screen.getByRole('searchbox', { name: /Search user content/i })).toBeTruthy()
    const primaryPane = contentViewer.closest('[data-workbench-primary-pane]')
    expect(primaryPane?.contains(screen.getByRole('group', { name: /Compact block map/i }))).toBe(true)
    expect(primaryPane?.contains(screen.getByRole('complementary', { name: /Block inspector/i }))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: /Response/i }))
    expect(await screen.findByRole('button', { name: /Assistant.*5 tokens/i })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /Request/i }))
    expect((await screen.findByRole('button', { name: /User.*position 2/i })).getAttribute('aria-pressed')).toBe('true')
  })

  it('filters blocks and exposes Raw in the same workbench', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks }), { status: 200 }))
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

  it('changes block size with the size selector', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_seq: 1, blocks }), { status: 200 }))
    renderWorkbench()

    const map = await screen.findByRole('group', { name: 'Compact block map' })
    const size = screen.getByRole('combobox', { name: 'Size' }) as HTMLSelectElement

    expect(size.value).toBe('26')
    expect(Array.from(size.options, (option) => option.text)).toEqual(['Smaller', 'Default', 'Larger'])
    expect(map.style.gridTemplateColumns).toContain('26px')

    await userEvent.selectOptions(size, '30')

    expect(size.value).toBe('30')
    expect(map.style.gridTemplateColumns).toContain('30px')
  })
})
