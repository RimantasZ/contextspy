import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeBlock } from '../../test/fixtures'
import { BlockInspector } from './BlockInspector'

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
})
