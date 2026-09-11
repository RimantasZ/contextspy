import type { Request } from '../../api/client'
import { formatRequestDuration } from '../../lib/format'

function statusTone(request: Request): string {
  if (request.invocation_outcome === 'completed' || (request.status_code != null && request.status_code >= 200 && request.status_code < 300)) return 'status-success'
  if (request.invocation_outcome === 'failed' || (request.status_code != null && request.status_code >= 500)) return 'status-danger'
  if (request.invocation_outcome === 'incomplete' || (request.status_code != null && request.status_code >= 400)) return 'status-warning'
  return ''
}

export function RequestSummaryHeader({ request, onBack, onDirection }: {
  request: Request
  onBack: () => void
  onDirection: (direction: 'input' | 'output') => void
}) {
  const cache = (request.cache_read_tokens ?? 0) + (request.cache_creation_tokens ?? 0)

  return (
    <header className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className="app-button min-h-9" aria-label="Go back">← Back</button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight">Request detail</h1>
            <span className={`app-badge ${statusTone(request)}`}>{request.status_code ?? request.invocation_outcome}</span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">{request.provider} · {request.model ?? 'Unknown model'} · {new Date(request.timestamp).toLocaleString()}</p>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-5">
        <button type="button" onClick={() => onDirection('input')} className="panel p-3 text-left hover:border-[var(--accent)]">
          <span className="eyebrow block">Context</span><strong className="text-lg tabular-nums">{request.tokens_total_input.toLocaleString()}</strong>
        </button>
        <button type="button" onClick={() => onDirection('output')} className="panel p-3 text-left hover:border-[var(--accent)]">
          <span className="eyebrow block">Generated</span><strong className="text-lg tabular-nums">{request.tokens_total_output.toLocaleString()}</strong>
        </button>
        <div className="panel p-3"><span className="eyebrow block">Duration</span><strong className="text-lg tabular-nums">{formatRequestDuration(request.duration_ms)}</strong></div>
        <div className="panel p-3"><span className="eyebrow block">Model</span><strong className="block truncate text-sm" title={request.model ?? undefined}>{request.model ?? '—'}</strong></div>
        <div className="panel col-span-2 p-3 sm:col-span-4 lg:col-span-1"><span className="eyebrow block">Cache activity</span><strong className="text-sm tabular-nums">{cache > 0 ? cache.toLocaleString() : 'None'}</strong></div>
      </div>
    </header>
  )
}
