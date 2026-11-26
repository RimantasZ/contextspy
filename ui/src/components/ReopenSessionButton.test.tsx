import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReopenSessionButton } from './ReopenSessionButton'

const state = vi.hoisted(() => ({ mutate: vi.fn(), data: {} as Record<string, unknown> }))

vi.mock('../api/hooks', () => ({
  useSessions: () => ({ data: state.data }),
  useReopenSession: () => ({ mutate: state.mutate, isPending: false, isError: false }),
}))

beforeEach(() => {
  state.mutate.mockClear()
  state.data = { reopenable_session_id: 's1', sessions: [{ id: 's2', name: 'New work', is_active: true }] }
})

describe('ReopenSessionButton', () => {
  it('reopens, and says which active session will be ended', async () => {
    render(<ReopenSessionButton sessionId="s1" className="" />)
    const button = screen.getByRole('button', { name: 'Reopen session' })
    expect(button.title).toContain("Ends the active session 'New work'")
    await userEvent.click(button)
    expect(state.mutate).toHaveBeenCalledWith('s1')
  })

  it('is hidden for every other session', () => {
    render(<ReopenSessionButton sessionId="s3" className="" />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
