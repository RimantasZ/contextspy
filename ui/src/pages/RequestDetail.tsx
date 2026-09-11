// Copyright 2026 Rimantas Zukaitis
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useRequest, useRequestToolStats } from '../api/hooks'
import { TokenDonut } from '../components/TokenDonut'
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown'
import { CaptureNotice } from '../components/request/CaptureNotice'
import { RequestSummaryHeader } from '../components/request/RequestSummaryHeader'
import { RequestWorkbench } from '../components/request/RequestWorkbench'
import type { WorkbenchDirection } from '../components/request/RequestWorkbench'

function categoryData(request: {
  tokens_system_prompt: number; tokens_tool_definitions: number; tokens_tool_results: number
  tokens_file_contents: number; tokens_conversation_history: number; tokens_current_user_message: number
  tokens_assistant_prefill: number; tokens_uncategorized: number
}): Record<string, number> {
  return {
    system_prompt: request.tokens_system_prompt,
    tool_definitions: request.tokens_tool_definitions,
    tool_results: request.tokens_tool_results,
    file_contents: request.tokens_file_contents,
    conversation_history: request.tokens_conversation_history,
    current_user_message: request.tokens_current_user_message,
    assistant_prefill: request.tokens_assistant_prefill,
    uncategorized: request.tokens_uncategorized,
  }
}

function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="surface overflow-hidden rounded-lg border border-[var(--border)]">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold hover:bg-[var(--surface-muted)]">{title}</summary>
      <div className="border-t border-[var(--border)] p-4">{children}</div>
    </details>
  )
}

export default function RequestDetail() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const requestQuery = useRequest(id)
  const toolStats = useRequestToolStats(id)
  const [activeDirection, setActiveDirection] = useState<WorkbenchDirection>('input')

  if (requestQuery.isLoading) return <div className="page-shell text-sm text-[var(--text-muted)]">Loading request…</div>
  if (requestQuery.error || !requestQuery.data) return <div className="page-shell text-sm text-[var(--danger)]">Request not found.</div>

  const request = requestQuery.data.request
  const categories = categoryData(request)
  const tools = toolStats.data?.tools ?? []
  const metadata = [
    ['Provider', request.provider],
    ['Agent', request.agent ?? '—'],
    ['Model', request.model ?? '—'],
    ['Endpoint', request.endpoint],
    ['Timestamp', new Date(request.timestamp).toLocaleString()],
    ['Status', request.status_code ?? request.invocation_outcome],
    ['Transport', `${request.transport} / ${request.response_transport}`],
    ['Time to first token', request.ttft_ms != null ? `${request.ttft_ms}ms` : '—'],
    ['Tokenizer', request.tokenizer],
    ['Session sequence', request.session_seq ?? '—'],
    ['Provider request ID', request.provider_response_id ?? '—'],
    ['Previous response ID', request.predecessor_response_id ?? '—'],
    ['API context tokens', request.provider_input_tokens?.toLocaleString() ?? '—'],
    ['API output tokens', request.provider_output_tokens?.toLocaleString() ?? '—'],
    ['API reasoning tokens', request.provider_reasoning_tokens?.toLocaleString() ?? '—'],
    ['Cache read / write', `${(request.cache_read_tokens ?? 0).toLocaleString()} / ${(request.cache_creation_tokens ?? 0).toLocaleString()}`],
    ['Visible coverage', request.context_accounting.visible_coverage_pct != null ? `${request.context_accounting.visible_coverage_pct.toFixed(1)}%` : '—'],
    ['Cached share', request.context_accounting.cached_share_pct != null ? `${request.context_accounting.cached_share_pct.toFixed(1)}%` : '—'],
  ]

  return (
    <div className="page-shell">
      <RequestSummaryHeader request={request} onBack={() => navigate(-1)} onDirection={setActiveDirection} />
      <CaptureNotice request={request} />
      <RequestWorkbench request={request} activeDirection={activeDirection} onDirectionChange={setActiveDirection} />

      <Disclosure title="Analytics">
        <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="panel">
            <h3 className="section-title mb-3">Category composition</h3>
            <TokenDonut data={categories} />
          </div>
          {tools.length > 0 ? <ToolBreakdownCharts tools={tools} totalInputTokens={request.tokens_total_input} /> : <div className="panel flex min-h-52 items-center justify-center text-sm text-[var(--text-muted)]">No tool usage recorded.</div>}
          {tools.length > 0 && <div className="xl:col-span-2"><ToolBreakdownTable tools={tools} totalInputTokens={request.tokens_total_input} /></div>}
        </div>
      </Disclosure>

      <Disclosure title="Metadata and diagnostics">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {metadata.map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt className="eyebrow mb-0.5">{label}</dt>
              <dd className="break-words font-medium tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
        {request.usage_extra && Object.keys(request.usage_extra).length > 0 && (
          <details className="mt-4 border-t border-[var(--border)] pt-3">
            <summary className="cursor-pointer text-xs font-medium text-[var(--text-muted)]">Additional API usage fields</summary>
            <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-[var(--surface-muted)] p-3 text-xs [overflow-wrap:anywhere] [white-space:pre-wrap]">{JSON.stringify(request.usage_extra, null, 2)}</pre>
          </details>
        )}
      </Disclosure>
    </div>
  )
}
