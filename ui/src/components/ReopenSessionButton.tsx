import { useReopenSession, useSessions } from '../api/hooks'

/** Renders only for the one session the server says can be reopened. */
export function ReopenSessionButton({ sessionId, className }: { sessionId: string; className: string }) {
  const { data } = useSessions()
  const reopen = useReopenSession()
  if (data?.reopenable_session_id !== sessionId) return null
  const active = data.sessions.find((s) => s.is_active)

  return (
    <>
      <button
        onClick={() => reopen.mutate(sessionId)}
        disabled={reopen.isPending}
        title={active ? `Ends the active session '${active.name}' and continues this one` : 'Continue recording into this session'}
        className={className}
      >
        Reopen session
      </button>
      {reopen.isError && <span role="alert" className="text-xs text-[var(--danger)]">Could not reopen this session</span>}
    </>
  )
}
