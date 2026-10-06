import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBlock, makeOccurrences } from '../../test/fixtures'
import { BlockInspector } from './BlockInspector'

afterEach(() => vi.restoreAllMocks())

describe('BlockInspector', () => {
  it('shows a block-shaped tool flow and invokes linked navigation', async () => {
    const jump = vi.fn()
    const definition = makeBlock({ id: 42, position: 0, block_type: 'tool_definition', tool_name: 'search', token_count: 120 })
    const call = makeBlock({ id: 43, position: 1, block_type: 'tool_call', tool_name: 'search', linked_definition_id: 42, tool_call_id: 'call-1' })
    const result = makeBlock({ id: 44, position: 2, block_type: 'tool_result', tool_name: 'search', linked_definition_id: 42, linked_call_id: 43, tool_call_id: 'call-1' })
    render(<BlockInspector block={call} blocks={[definition, call, result]} onJump={jump} onClear={() => {}} />)

    expect(screen.getByText('Tool relationship')).toBeTruthy()
    expect(screen.getByLabelText('Selected Tool call: search')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Jump to Tool definition: search' }))
    expect(jump).toHaveBeenCalledWith(42)
    await userEvent.click(screen.getByRole('button', { name: 'Jump to Tool result: search' }))
    expect(jump).toHaveBeenCalledWith(44)
  })

  it('prompts for a selection when no block is active', () => {
    render(<BlockInspector block={null} blocks={[]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText(/metadata and relationships/i)).toBeTruthy()
  })

  it('shows a per-request header stored outside the block content', () => {
    const block = makeBlock({ id: 1, block_type: 'system_prompt', attrs: { volatile_header: 'x-anthropic-billing-header: cch=1;\n' } })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText('Per-request header (not part of block identity)')).toBeTruthy()
    expect(screen.getByText('x-anthropic-billing-header: cch=1;')).toBeTruthy()
  })

  it('omits the header row for ordinary blocks', () => {
    const block = makeBlock({ id: 1, block_type: 'system_prompt' })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.queryByText(/Per-request header/)).toBeNull()
  })

  it('shows source, activity and the raw JSON location when they are known', () => {
    const block = makeBlock({
      id: 5, block_type: 'tool_call', tool_name: 'exec', source_key: 'exec:multi', activity: 'command',
      json_path: ['input', 4, 'content', 1], attrs: { source: { calls: ['git', 'rg'] } },
    })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText('exec:multi')).toBeTruthy()
    expect(screen.getByText('command')).toBeTruthy()
    expect(screen.getByText('git, rg')).toBeTruthy()
    expect(screen.getByText('input[4].content[1]')).toBeTruthy()
  })

  it('shows the file a read/edit tool call targets, and every file of a multi-file call', () => {
    const block = makeBlock({
      id: 7, block_type: 'tool_call', tool_name: 'apply_patch', file_path: 'src/a.py',
      attrs: { source: { files: ['src/a.py', 'b.txt'] } },
    })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText('src/a.py', { selector: 'dd' })).toBeTruthy()
    expect(screen.getByText('src/a.py, b.txt')).toBeTruthy()
  })

  it('omits the file rows when no file is known', () => {
    const block = makeBlock({ id: 8, block_type: 'tool_call' })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.queryByText('File')).toBeNull()
    expect(screen.queryByText('Files in call')).toBeNull()
  })

  it('omits source rows for blocks that were captured before classification existed', () => {
    const block = makeBlock({ id: 6 })
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.queryByText('Source')).toBeNull()
    expect(screen.queryByText('Activity')).toBeNull()
    expect(screen.queryByText('Raw JSON location')).toBeNull()
  })

  describe('Present in section', () => {
    function renderInspector(block: ReturnType<typeof makeBlock>, requestId?: string) {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(makeOccurrences()), { status: 200 }))
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      return render(<QueryClientProvider client={client}><BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} requestId={requestId} /></QueryClientProvider>)
    }

    it('is shown for input blocks of a known request', async () => {
      renderInspector(makeBlock({ id: 9 }), 'request-1')
      expect(await screen.findByText(/of 5 requests/)).toBeTruthy()
      expect(screen.getByRole('region', { name: 'Present in' })).toBeTruthy()
    })

    it('is hidden for output blocks and without a request id', () => {
      renderInspector(makeBlock({ id: 9, direction: 'output' }), 'request-1').unmount()
      expect(screen.queryByRole('region', { name: 'Present in' })).toBeNull()
      renderInspector(makeBlock({ id: 9 }))
      expect(screen.queryByRole('region', { name: 'Present in' })).toBeNull()
    })
  })
})

