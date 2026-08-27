// Copyright 2026 Rimantas Zukaitis
// Licensed under the Apache License, Version 2.0
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useLogicalRequest } from '../api/hooks'
import { TokenDonut } from '../components/TokenDonut'
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown'

const CATEGORY_LABELS: Record<string, string> = {
  system_prompt: 'System Prompt',
  tool_definitions: 'Tool Definitions',
  tool_results: 'Tool Results',
  file_contents: 'File Contents',
  conversation_history: 'Conversation History',
  current_user_message: 'Current User Message',
  assistant_prefill: 'Assistant Prefill',
  uncategorized: 'Uncategorized',
  protocol_overhead: 'Protocol Overhead',
}

function number(value: number | null | undefined): string {
  return value == null ? '—' : value.toLocaleString()
}

export default function LogicalRequestDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const query = useLogicalRequest(id ?? '')
  const [showContext, setShowContext] = useState(false)

  if (query.isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (!query.data) return <div className="p-6 text-red-400">Logical request not found.</div>

  const logical = query.data.logical_request
  const invocations = query.data.invocations
  const context = query.data.context
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

      {context && (
        <div className="space-y-4">
          <div>
            <h2 className="text-base font-semibold text-white">Effective context composition</h2>
            <p className="text-xs text-gray-500">
              {context.selection === 'largest_reconstructed_snapshot'
                ? `Best captured snapshot (model call ${context.invocation_seq ?? '—'}); run contextspy db-upgrade to repair older unresolved lineage.`
                : `Context presented to model call ${context.invocation_seq ?? '—'}, reconstructed across the WebSocket lineage.`}
            </p>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-gray-800 rounded-lg p-4">
              <TokenDonut data={context.composition.by_category} />
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <p className="text-sm font-medium text-gray-300 mb-3">Category breakdown</p>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-gray-400 border-b border-gray-700">
                  <th className="pb-2">Category</th><th className="pb-2 text-right">Tokens</th><th className="pb-2 text-right">%</th>
                </tr></thead>
                <tbody>{Object.entries(context.composition.by_category)
                  .filter(([, value]) => value > 0)
                  .sort(([, a], [, b]) => b - a)
                  .map(([category, value]) => (
                    <tr key={category} className="border-b border-gray-700/40">
                      <td className="py-1.5 text-gray-300">{CATEGORY_LABELS[category] ?? category}</td>
                      <td className="py-1.5 text-right text-gray-300">{value.toLocaleString()}</td>
                      <td className="py-1.5 text-right text-gray-400">{context.composition.total_tokens > 0 ? `${(value / context.composition.total_tokens * 100).toFixed(1)}%` : '—'}</td>
                    </tr>
                  ))}</tbody>
              </table>
            </div>
          </div>
          {context.tools.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <ToolBreakdownCharts tools={context.tools} />
              <ToolBreakdownTable tools={context.tools} totalInputTokens={context.composition.total_tokens} />
            </div>
          )}
          <div className="bg-gray-800 rounded-lg p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-300">Reconstructed context blocks</p>
                <p className="text-xs text-gray-500">Includes inherited messages, tool calls, tool responses, and current input.</p>
              </div>
              <button onClick={() => setShowContext(value => !value)} className="text-xs text-indigo-300 hover:text-indigo-200">
                {showContext ? 'Hide blocks' : `Show ${context.blocks.length} blocks`}
              </button>
            </div>
            {showContext && (
              <div className="max-h-[560px] overflow-auto border border-gray-700 rounded mt-4">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-gray-900"><tr className="text-left text-gray-400">
                    <th className="p-2">#</th><th className="p-2">Source</th><th className="p-2">Category</th><th className="p-2">Tool</th><th className="p-2 text-right">Tokens</th><th className="p-2">Content</th>
                  </tr></thead>
                  <tbody>{context.blocks.map(block => (
                    <tr key={block.id} className="border-t border-gray-800 align-top">
                      <td className="p-2 text-gray-600">{block.position + 1}</td>
                      <td className="p-2 text-indigo-300 whitespace-nowrap">{block.provenance}</td>
                      <td className="p-2 text-gray-400 whitespace-nowrap">{CATEGORY_LABELS[block.category ?? ''] ?? block.block_type}</td>
                      <td className="p-2 text-gray-400 whitespace-nowrap">{block.tool_name ?? '—'}</td>
                      <td className="p-2 text-right text-gray-300">{block.token_count.toLocaleString()}</td>
                      <td className="p-2 text-gray-300 font-mono whitespace-pre-wrap break-all max-w-xl">{block.content ?? (block.content_purged ? '[purged]' : '[hidden]')}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

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
