import { useEffect, useRef } from 'react'
import { useArchiveSession } from '../api/hooks'
import { formatBytes } from '../lib/format'

interface Props {
  sessionId: string
  sessionName: string
  onClose: () => void
  onArchived?: () => void
}

/** Confirmation (and result) dialog for the one-way session archive. */
export function ArchiveSessionModal({ sessionId, sessionName, onClose, onArchived }: Props) {
  const archive = useArchiveSession()
  const cancelRef = useRef<HTMLButtonElement>(null)
  const result = archive.data

  useEffect(() => { cancelRef.current?.focus() }, [])
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && !archive.isPending) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, archive.isPending])

  function handleArchive() {
    archive.mutate(sessionId, { onSuccess: () => onArchived?.() })
  }

  return (
    <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !archive.isPending) onClose() }}>
      <div className="modal-dialog mx-4 w-full max-w-md space-y-4" role="dialog" aria-modal="true" aria-labelledby="archive-session-title">
        <h2 id="archive-session-title" className="text-base font-semibold">
          {result ? 'Session archived' : <>Archive session &ldquo;{sessionName}&rdquo;</>}
        </h2>

        {result ? (
          <div className="space-y-2 text-sm text-[var(--text)]">
            <p>
              Removed {formatBytes(result.freed.request_body_bytes)} of request and response payloads from{' '}
              {result.freed.requests.toLocaleString()} {result.freed.requests === 1 ? 'request' : 'requests'} and{' '}
              {formatBytes(result.freed.content_bytes)} of block text ({result.freed.content_rows.toLocaleString()} entries).
              {result.already_archived && ' The session was already archived; this removed anything captured since.'}
            </p>
            {result.space.auto_vacuum === 'incremental' && (
              <p className="text-[var(--text-muted)]">File space returned to the disk: {formatBytes(result.space.reclaimed_bytes)}.</p>
            )}
            {result.space.note && <p className="text-[var(--text-muted)]">{result.space.note}</p>}
          </div>
        ) : (
          <div className="space-y-3 text-sm">
            <p className="text-[var(--text)]">
              This removes the raw request and response payloads and the stored text of the blocks in this session.
              <strong> It cannot be undone.</strong>
            </p>
            <p className="text-[var(--text-muted)]">
              Kept: token counts, block structure and categories, tool and source labels, JSON locations, and the conversation and lineage analysis.
              Only a database backup made earlier (<code>contextspy db-backup</code>) still contains the removed content.
            </p>
            <p className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] p-2.5 text-xs text-[var(--text-muted)]">
              If you may continue a conversation from this session later, don&rsquo;t archive it: a continuation whose
              predecessor was archived is recorded with partial context.
            </p>
            {archive.isError && <p role="alert" className="text-[var(--danger)]">Could not archive this session: {archive.error instanceof Error ? archive.error.message : 'unknown error'}.</p>}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          {result ? (
            <button ref={cancelRef} onClick={onClose} className="app-button">Close</button>
          ) : (
            <>
              <button ref={cancelRef} onClick={onClose} disabled={archive.isPending} className="app-button">Cancel</button>
              <button onClick={handleArchive} disabled={archive.isPending} className="app-button-danger">
                {archive.isPending ? 'Archiving…' : 'Archive session'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
