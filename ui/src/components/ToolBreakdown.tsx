// Copyright 2026 Rimantas Zukaitis
import { useMemo, useState } from 'react'
import type { ToolStat } from '../api/client'
import { ToolTreemap, toolColor } from './ToolTreemap'

type SortCol = 'tool' | 'def' | 'result' | 'total' | 'pct'
type SortDir = 'asc' | 'desc'

function SortHeader({ label, col, sortCol, sortDir, onSort, className = '' }: {
  label: string
  col: SortCol
  sortCol: SortCol
  sortDir: SortDir
  onSort: (col: SortCol) => void
  className?: string
}) {
  const active = sortCol === col
  return (
    <th aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'} className={`border-b border-[var(--border)] p-2 font-medium ${className}`}>
      <button type="button" onClick={() => onSort(col)} className="inline-flex items-center gap-1 whitespace-nowrap hover:text-[var(--text)]">
        {label}{active && <span aria-hidden="true">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </button>
    </th>
  )
}

export function ToolBreakdownCharts({ tools }: { tools: ToolStat[] }) {
  return (
    <div className="panel">
      <h3 className="section-title mb-3">Tool composition</h3>
      <ToolTreemap tools={tools} />
    </div>
  )
}

export function ToolBreakdownTable({ tools, totalInputTokens }: { tools: ToolStat[]; totalInputTokens?: number }) {
  const [sortCol, setSortCol] = useState<SortCol>('total')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [showAll, setShowAll] = useState(false)
  const total = tools.reduce((sum, tool) => sum + tool.definition_tokens + tool.result_tokens, 0)

  const rows = useMemo(() => [...tools].map((tool) => ({ tool, total: tool.definition_tokens + tool.result_tokens })).sort((a, b) => {
    let comparison = 0
    if (sortCol === 'tool') comparison = a.tool.tool_name.localeCompare(b.tool.tool_name)
    else if (sortCol === 'def') comparison = a.tool.definition_tokens - b.tool.definition_tokens
    else if (sortCol === 'result') comparison = a.tool.result_tokens - b.tool.result_tokens
    else comparison = a.total - b.total
    return sortDir === 'asc' ? comparison : -comparison
  }), [tools, sortCol, sortDir])

  function sort(col: SortCol) {
    if (sortCol === col) setSortDir((current) => current === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir(col === 'tool' ? 'asc' : 'desc') }
  }

  if (total === 0) return <div className="panel flex min-h-52 items-center justify-center text-sm italic text-[var(--text-muted)]">No tool usage recorded yet.</div>
  const visible = showAll ? rows : rows.slice(0, 8)

  return (
    <div className="panel">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="section-title">Exact tool values</h3>
        {rows.length > 8 && <button type="button" onClick={() => setShowAll((current) => !current)} className="app-button min-h-8 py-1 text-xs">{showAll ? 'Show top 8' : `Show all ${rows.length}`}</button>}
      </div>
      <div className="max-h-[310px] overflow-auto">
        <table className="w-full min-w-[460px] text-xs">
          <thead className="sticky top-0 z-10 bg-[var(--surface)] text-[var(--text-muted)]">
            <tr>
              <SortHeader label="Tool" col="tool" sortCol={sortCol} sortDir={sortDir} onSort={sort} className="text-left" />
              <SortHeader label="Definitions" col="def" sortCol={sortCol} sortDir={sortDir} onSort={sort} className="text-right" />
              <SortHeader label="Results" col="result" sortCol={sortCol} sortDir={sortDir} onSort={sort} className="text-right" />
              <SortHeader label="Total" col="total" sortCol={sortCol} sortDir={sortDir} onSort={sort} className="text-right" />
              <SortHeader label="Context" col="pct" sortCol={sortCol} sortDir={sortDir} onSort={sort} className="text-right" />
            </tr>
          </thead>
          <tbody>
            {visible.map(({ tool, total: combined }) => (
              <tr key={tool.tool_name} className="border-b border-[var(--border)] last:border-0">
                <td className="p-2"><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm border border-[var(--graphical-border)]" style={{ background: toolColor(tool.tool_name) }} /><span className="break-all">{tool.tool_name}</span></td>
                <td className="p-2 text-right tabular-nums">{tool.definition_tokens.toLocaleString()}</td>
                <td className="p-2 text-right tabular-nums">{tool.result_tokens.toLocaleString()}</td>
                <td className="p-2 text-right font-medium tabular-nums">{combined.toLocaleString()}</td>
                <td className="p-2 text-right tabular-nums text-[var(--text-muted)]">{totalInputTokens && totalInputTokens > 0 ? `${((combined / totalInputTokens) * 100).toFixed(1)}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
