import type { Request } from '../../api/client'

export function CaptureNotice({ request }: { request: Request }) {
  const hasNotice = request.context_fidelity !== 'complete' || request.context_notes.length > 0 || request.capture_error || request.response_reconstructed || !request.response_complete
  if (!hasNotice) return null

  const severe = request.context_fidelity === 'opaque' || Boolean(request.capture_error)
  return (
    <div className={severe ? 'notice-danger' : 'notice-warning'} role="status">
      <div className="font-semibold">{request.context_fidelity === 'opaque' ? 'Opaque request context' : request.capture_error ? 'Capture warning' : 'Capture notes'}</div>
      <div className="mt-1 text-xs leading-5">
        {request.capture_error && <span>{String(request.capture_error.message ?? request.capture_error.stage ?? 'Some payload data could not be captured.')}. </span>}
        {request.context_notes.join(' ')}
        {request.response_reconstructed && <span> Response reconstructed from {request.response_transport || 'stream'}.</span>}
        {!request.response_complete && <span> Response capture is incomplete.</span>}
      </div>
    </div>
  )
}
