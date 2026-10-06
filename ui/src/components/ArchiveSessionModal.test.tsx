import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ArchiveResult } from '../api/client'
import { ArchiveSessionModal } from './ArchiveSessionModal'

const session = { id: 's1', name: 'Old work', started_at: '2026-10-01T00:00:00', ended_at: '2026-10-01T01:00:00', is_active: false, status: 'archived', archived_at: '2026-10-06T08:00:00' } as const

function result(overrides: Partial<ArchiveResult> = {}): ArchiveResult {
  return {
    session, already_archived: false,
    freed: { requests: 3, request_body_bytes: 3 * 1024 * 1024, content_rows: 5, content_bytes: 2048 },
    space: { auto_vacuum: 'incremental', reclaimed_bytes: 4 * 1024 * 1024, free_bytes_remaining: 0, note: null },
    ...overrides,
  }
}

function renderModal(props: Partial<{ onClose: () => void; onArchived: () => void }> = {}) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = props.onClose ?? vi.fn()
  render(<QueryClientProvider client={client}><ArchiveSessionModal sessionId="s1" sessionName="Old work" onClose={onClose} onArchived={props.onArchived} /></QueryClientProvider>)
  return { onClose, invalidate }
}

afterEach(() => vi.restoreAllMocks())

describe('ArchiveSessionModal', () => {
  it('states what is removed and kept, that it is final, and the resume and backup caveats', () => {
    renderModal()
    const dialog = screen.getByRole('dialog', { name: /Archive session .Old work./ })
    expect(dialog.textContent).toContain('It cannot be undone.')
    expect(dialog.textContent).toContain('Kept: token counts, block structure and categories')
    expect(dialog.textContent).toContain('contextspy db-backup')
    expect(dialog.textContent).toContain('predecessor was archived is recorded with partial context')
    expect(screen.getByRole('button', { name: 'Archive session' })).toBeTruthy()
  })

  it('focuses Cancel, and Cancel or Escape close without archiving', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    const { onClose } = renderModal()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(2)
    expect(spy).not.toHaveBeenCalled()
  })

  it('archives, refreshes the app and shows what was removed and returned', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(result()), { status: 200 }))
    const onArchived = vi.fn()
    const { invalidate } = renderModal({ onArchived })
    await userEvent.click(screen.getByRole('button', { name: 'Archive session' }))

    expect(await screen.findByText('Session archived')).toBeTruthy()
    expect(String(spy.mock.calls[0][0])).toContain('/sessions/s1/archive')
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST')
    expect(screen.getByText(/Removed 3.0 MiB of request and response payloads from 3 requests and 2.0 KiB of block text \(5 entries\)/)).toBeTruthy()
    expect(screen.getByText('File space returned to the disk: 4.0 MiB.')).toBeTruthy()
    expect(onArchived).toHaveBeenCalled()
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0])
    expect(keys).toEqual(expect.arrayContaining(['sessions', 'stats', 'request', 'requests']))
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy()
  })

  it('shows the server note when the file cannot shrink by itself, and says when it was already archived', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(result({
      already_archived: true,
      freed: { requests: 1, request_body_bytes: 10, content_rows: 0, content_bytes: 0 },
      space: { auto_vacuum: 'none', reclaimed_bytes: 0, free_bytes_remaining: 99, note: 'Stop ContextSpy and run `contextspy db-compact`.' },
    })), { status: 200 }))
    renderModal()
    await userEvent.click(screen.getByRole('button', { name: 'Archive session' }))
    expect(await screen.findByText(/already archived; this removed anything captured since/)).toBeTruthy()
    expect(screen.getByText(/contextspy db-compact/)).toBeTruthy()
    expect(screen.queryByText(/File space returned/)).toBeNull()
    expect(screen.getByText(/from 1 request and/)).toBeTruthy()
  })

  it('disables the buttons while archiving and shows server errors', async () => {
    let finish: (response: Response) => void = () => {}
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const { onClose } = renderModal()
    await userEvent.click(screen.getByRole('button', { name: 'Archive session' }))
    expect((screen.getByRole('button', { name: 'Archiving…' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
    finish(new Response(JSON.stringify({ detail: 'End the session before archiving it' }), { status: 409 }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toContain('Could not archive this session')
    expect((screen.getByRole('button', { name: 'Archive session' }) as HTMLButtonElement).disabled).toBe(false)
  })
})
