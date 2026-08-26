// Copyright 2026 Rimantas Zukaitis
// Licensed under the Apache License, Version 2.0
import { useNavigate, useParams } from 'react-router-dom'
import { useLogicalRequest } from '../api/hooks'

function number(value: number | null | undefined): string {
  return value == null ? '—' : value.toLocaleString()
}

export default function LogicalRequestDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const query = useLogicalRequest(id ?? '')

  if (query.isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (!query.data) return <div className="p-6 text-red-400">Logical request not found.</div>

  const logical = query.data.logical_request
  const invocations = query.data.invocations
  const cacheRate = logical.cumulative_input_tokens && logical.cumulative_cached_tokens != null
    ? logical.cumulative_cached_tokens / logical.cumulative_input_tokens * 100
    : null

  const stats = [
    ['Model calls', logical.invocation_count.toLocaleString()],
    ['Peak context', number(logical.peak_context_tokens)],
    ['Final context', number(logical.final_context_tokens)],
    ['Cumulative input', number(logical.cumulative_input_tokens)],
    ['Cached input', number(logical.cumulative_cached_tokens)],
    ['Cache hit', cacheRate == null ? '—' : `${cacheRate.toFixed(1)}%`],
    ['Cache writes', number(logical.cumulative_cache_write_tokens)],
    ['Output', number(logical.cumulative_output_tokens)],
    ['Reasoning', number(logical.cumulative_reasoning_tokens)],
  ]

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="text-gray-400 hover:text-white text-sm">← Back</button>
        <div>
          <h1 className="text-xl font-bold text-white">Logical request</h1>
          <p className="text-xs text-gray-500">{logical.provider} · {logical.agent ?? 'unknown agent'} · {logical.model ?? 'unknown model'}</p>
        </div>
        {logical.invocation_count > 1 && <span className="px-2 py-1 rounded bg-indigo-900 text-indigo-300 text-xs">{logical.invocation_count} model calls</span>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {stats.map(([label, value]) => (
          <div key={label} className="bg-gray-800 rounded-lg p-3">
            <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
            <p className="text-lg text-white font-semibold mt-1">{value}</p>
          </div>
        ))}
      </div>

      <div className="bg-gray-800 rounded-lg p-4 text-sm grid grid-cols-2 md:grid-cols-4 gap-4">
        <div><p className="text-xs text-gray-500">State</p><p className="text-white">{logical.state}</p></div>
        <div><p className="text-xs text-gray-500">Grouping</p><p className="text-white">{logical.grouping_confidence}</p></div>
        <div><p className="text-xs text-gray-500">Conversation</p><p className="text-white font-mono text-xs break-all">{logical.provider_conversation_id ?? '—'}</p></div>
        <div><p className="text-xs text-gray-500">Turn</p><p className="text-white font-mono text-xs break-all">{logical.logical_turn_id ?? '—'}</p></div>
      </div>

      <div className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-white">Model invocation timeline</h2>
          <p className="text-xs text-gray-500">Each row is a real provider model call. Cumulative input is the sum of provider-reported input across these calls.</p>
        </div>
        <div className="overflow-x-auto bg-gray-800 rounded-lg p-4">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-gray-400 border-b border-gray-700">
              <th className="pb-2 pr-3">#</th>
              <th className="pb-2 pr-3 text-right">Observed</th>
              <th className="pb-2 pr-3 text-right">Reconstructed</th>
              <th className="pb-2 pr-3 text-right">Provider input</th>
              <th className="pb-2 pr-3 text-right">Unattributed</th>
              <th className="pb-2 pr-3 text-right">Cached</th>
              <th className="pb-2 pr-3 text-right">Output</th>
              <th className="pb-2 pr-3">Lineage</th>
              <th className="pb-2">Provider response</th>
            </tr></thead>
            <tbody>{invocations.map(invocation => (
              <tr key={invocation.id} onClick={() => navigate(`/requests/${invocation.id}`)} className="border-b border-gray-700 hover:bg-gray-750 cursor-pointer">
                <td className="py-2 pr-3 text-indigo-300">{invocation.invocation_seq ?? '—'}</td>
                <td className="py-2 pr-3 text-right text-gray-300">{number(invocation.observed_input_tokens)}</td>
                <td className="py-2 pr-3 text-right text-gray-300">{number(invocation.reconstructed_input_tokens)}</td>
                <td className="py-2 pr-3 text-right text-white font-medium">{number(invocation.provider_input_tokens)}</td>
                <td className="py-2 pr-3 text-right text-amber-300">{number(invocation.unattributed_input_tokens)}</td>
                <td className="py-2 pr-3 text-right text-teal-300">{number(invocation.cache_read_tokens)}</td>
                <td className="py-2 pr-3 text-right text-gray-300">{number(invocation.provider_output_tokens)}</td>
                <td className="py-2 pr-3 text-gray-400">{invocation.lineage_status}</td>
                <td className="py-2 font-mono text-xs text-gray-500 max-w-[180px] truncate" title={invocation.provider_request_id ?? undefined}>{invocation.provider_request_id ?? '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
