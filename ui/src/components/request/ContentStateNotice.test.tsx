import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeRequest } from '../../test/fixtures'
import { ContentStateNotice } from './ContentStateNotice'

describe('ContentStateNotice', () => {
  it('explains an archived session, with the date when known', () => {
    render(<ContentStateNotice request={makeRequest({ content_state: 'archived', session_archived_at: '2026-10-06T08:00:00' })} />)
    expect(screen.getByText('Archived session')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toMatch(/archived on .*raw payloads and\s+block text were removed/)
    expect(screen.getByRole('status').textContent).toContain('Token counts, block structure and analysis remain')
  })

  it('handles an archived session without a date', () => {
    render(<ContentStateNotice request={makeRequest({ content_state: 'archived' })} />)
    expect(screen.getByRole('status').textContent).toMatch(/This session was archived: raw payloads/)
  })

  it('says neutrally that payloads are not stored when they were never kept or were purged', () => {
    render(<ContentStateNotice request={makeRequest({ content_state: 'not_retained' })} />)
    expect(screen.getByRole('status').textContent).toContain('no longer stored')
    expect(screen.queryByText('Archived session')).toBeNull()
  })

  it('shows nothing when the payloads are stored or the state is unknown', () => {
    const { container, rerender } = render(<ContentStateNotice request={makeRequest({ content_state: 'retained' })} />)
    expect(container.textContent).toBe('')
    rerender(<ContentStateNotice request={makeRequest()} />)
    expect(container.textContent).toBe('')
  })
})
