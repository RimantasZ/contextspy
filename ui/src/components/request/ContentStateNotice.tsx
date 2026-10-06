import type { Request } from '../../api/client'
import { formatDateTime } from '../../lib/format'

/** Explains why a request's stored payloads are missing (archived session vs. simply not stored). */
export function ContentStateNotice({ request }: { request: Request }) {
  if (request.content_state === 'archived') {
    return (
      <div className="notice-warning" role="status">
        <div className="font-semibold">Archived session</div>
        <div className="mt-1 text-xs leading-5">
          This session was archived{request.session_archived_at ? ` on ${formatDateTime(request.session_archived_at)}` : ''}: raw payloads and
          block text were removed. Token counts, block structure and analysis remain.
        </div>
      </div>
    )
  }
  if (request.content_state === 'not_retained') {
    return (
      <div className="notice" role="status">
        <div className="text-xs leading-5">Raw payloads for this request are no longer stored. Token counts, block structure and analysis remain.</div>
      </div>
    )
  }
  return null
}
