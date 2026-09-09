import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeBlock } from '../../test/fixtures'
import { BlockInspector } from './BlockInspector'

describe('BlockInspector', () => {
  it('shows details and invokes linked navigation', async () => {
    const jump = vi.fn()
    render(<BlockInspector block={makeBlock({ tool_name: 'search', linked_call_id: 42 })} onJump={jump} onClear={() => {}} />)
    expect(screen.getByText('10')).toBeTruthy()
    expect(screen.getByText('search')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Jump to tool call' }))
    expect(jump).toHaveBeenCalledWith(42)
  })

  it('distinguishes purged and missing content', () => {
    const { rerender } = render(<BlockInspector block={makeBlock({ content: null, content_purged: true })} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText(/purged, but its structure/i)).toBeTruthy()
    rerender(<BlockInspector block={makeBlock({ content: null, content_purged: false })} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText(/No content was captured/i)).toBeTruthy()
  })

  it('tokenizes only when highlighting is requested', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ results: [['hello', ' world']] }), { status: 200 }))
    render(<BlockInspector block={makeBlock()} onJump={() => {}} onClear={() => {}} />)
    expect(fetchMock).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('checkbox', { name: /Token highlight/i }))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockRestore()
  })
})
