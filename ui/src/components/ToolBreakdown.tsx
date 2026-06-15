// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useState } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import type { ToolStat } from '../api/client'

const PALETTE = [
  '#818cf8', '#34d399', '#f59e0b', '#f87171', '#38bdf8', '#a78bfa',
  '#fb923c', '#4ade80', '#e879f9', '#facc15', '#2dd4bf', '#f472b6',
]
export const toolColor = (i: number) => PALETTE[i % PALETTE.length]

function ToolLegend({ tools }: { tools: ToolStat[] }) {
  const cols = tools.length > 20 ? 3 : tools.length > 10 ? 2 : 1
  const perCol = Math.ceil(tools.length / cols)
  const columns: { tool: ToolStat; idx: number }[][] = Array.from({ length: cols }, (_, c) =>
    tools.slice(c * perCol, (c + 1) * perCol).map((tool, r) => ({ tool, idx: c * perCol + r }))
  )
  return (
    <div className="flex gap-3 shrink-0 pr-4 border-r border-gray-700 overflow-y-auto">
      {columns.map((col, ci) => (
        <div key={ci} className="flex flex-col gap-1">
          {col.map(({ tool, idx }) => (
            <div key={tool.tool_name} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: toolColor(idx) }} />
              <span className="text-xs text-gray-300 truncate max-w-[110px]" title={tool.tool_name}>
                {tool.tool_name.length > 16 ? tool.tool_name.slice(0, 14) + '…' : tool.tool_name}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

interface DonutProps {
  title: string
  data: { name: string; value: number; idx: number }[]
  total: number
  placeholder?: string
}

function Donut({ title, data, total, placeholder }: DonutProps) {
  return (
    <div className="flex flex-col items-center flex-1">
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-1">{title}</p>
      {total === 0 ? (
        <div className="flex items-center justify-center flex-1 w-full text-gray-500 text-xs italic border border-gray-700 rounded-lg px-2 text-center min-h-[140px]">
          {placeholder ?? `No ${title.toLowerCase()}`}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%"
              innerRadius={48} outerRadius={76} paddingAngle={2}>
              {data.map(entry => <Cell key={entry.name} fill={toolColor(entry.idx)} />)}
            </Pie>
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '6px' }}
              labelStyle={{ color: '#f9fafb' }}
              itemStyle={{ color: '#d1d5db' }}
              formatter={(val: number) => [
                `${val.toLocaleString()} (${total > 0 ? ((val / total) * 100).toFixed(1) : 0}%)`,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Panel 1: legend + two donuts ─────────────────────────────────────────────
export function ToolBreakdownCharts({ tools }: { tools: ToolStat[] }) {
  const totalDef = tools.reduce((s, t) => s + t.definition_tokens, 0)
  const totalRes = tools.reduce((s, t) => s + t.result_tokens, 0)

  const defData = tools.map((t, i) => ({ name: t.tool_name, value: t.definition_tokens, idx: i })).filter(d => d.value > 0)
  const resData = tools.map((t, i) => ({ name: t.tool_name, value: t.result_tokens, idx: i })).filter(d => d.value > 0)

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-sm font-medium text-gray-300 mb-3">Tool token breakdown</p>
      {totalDef === 0 && totalRes === 0 ? (
        <div className="h-48 flex items-center justify-center text-gray-500 text-sm italic">No tool usage recorded yet.</div>
      ) : (
        <div className="flex gap-3 h-[200px]">
          <ToolLegend tools={tools} />
          <Donut title="Definitions" data={defData} total={totalDef} />
          <Donut title="Results" data={resData} total={totalRes} placeholder="No tool results" />
        </div>
      )}
    </div>
  )
}

type SortCol = 'tool' | 'def' | 'result' | 'pct'
type SortDir = 'asc' | 'desc'

function SortHeader({ label, col, sortCol, sortDir, onSort, className = '' }: {
  label: string
  col: SortCol
  sortCol: SortCol | null
  sortDir: SortDir
  onSort: (col: SortCol) => void
  className?: string
}) {
  const active = sortCol === col
  return (
    <th
      className={`pb-2 font-medium border-b border-gray-700 whitespace-nowrap cursor-pointer select-none hover:text-gray-300 ${className}`}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && <span className="text-indigo-400">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </span>
    </th>
  )
}

// ── Panel 2: table ───────────────────────────────────────────────────────────
export function ToolBreakdownTable({ tools, totalInputTokens }: { tools: ToolStat[]; totalInputTokens?: number }) {
  const [sortCol, setSortCol] = useState<SortCol | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  const totalDef = tools.reduce((s, t) => s + t.definition_tokens, 0)
  const totalRes = tools.reduce((s, t) => s + t.result_tokens, 0)

  function handleSort(col: SortCol) {
    let newCol: SortCol | null
    let newDir: SortDir
    if (sortCol === col) {
      if (sortDir === 'asc') { newCol = col;  newDir = 'desc' }
      else                   { newCol = null; newDir = 'asc'  }
    } else {
      newCol = col; newDir = 'asc'
    }
    setSortCol(newCol)
    setSortDir(newDir)
  }

  const mapped = tools.map((t, i) => ({ t, i, combined: t.definition_tokens + t.result_tokens }))
  const rows = sortCol
    ? [...mapped].sort((a, b) => {
        let diff = 0
        if (sortCol === 'tool') diff = a.t.tool_name.localeCompare(b.t.tool_name)
        else if (sortCol === 'def') diff = a.t.definition_tokens - b.t.definition_tokens
        else if (sortCol === 'result') diff = a.t.result_tokens - b.t.result_tokens
        else diff = a.combined - b.combined
        return sortDir === 'asc' ? diff : -diff
      })
    : mapped

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-sm font-medium text-gray-300 mb-3">Tool usage table</p>
      {totalDef === 0 && totalRes === 0 ? (
        <div className="h-48 flex items-center justify-center text-gray-500 text-sm italic">No tool usage recorded yet.</div>
      ) : (
        <div className="overflow-auto max-h-[220px]">
          <table className="w-full text-xs text-gray-300 border-separate border-spacing-0">
            <thead className="sticky top-0 bg-gray-800 z-10">
              <tr className="text-gray-500 uppercase tracking-wide">
                <SortHeader label="Tool"          col="tool"   sortCol={sortCol} sortDir={sortDir} onSort={handleSort} className="text-left pr-3" />
                <SortHeader label="Def tokens"    col="def"    sortCol={sortCol} sortDir={sortDir} onSort={handleSort} className="text-right pr-3" />
                <SortHeader label="Result tokens" col="result" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} className="text-right pr-3" />
                <SortHeader label="% context"     col="pct"    sortCol={sortCol} sortDir={sortDir} onSort={handleSort} className="text-right" />
              </tr>
            </thead>
            <tbody>
              {rows.map(({ t, i, combined }) => {
                const pct = totalInputTokens && totalInputTokens > 0
                  ? ((combined / totalInputTokens) * 100).toFixed(1)
                  : null
                return (
                  <tr key={t.tool_name} className="hover:bg-gray-700/30">
                    <td className="py-1.5 pr-3 border-b border-gray-700/40">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: toolColor(i) }} />
                        <span className="truncate max-w-[180px]" title={t.tool_name}>{t.tool_name}</span>
                      </div>
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums border-b border-gray-700/40">
                      {t.definition_tokens.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums border-b border-gray-700/40">
                      {t.result_tokens > 0 ? t.result_tokens.toLocaleString() : <span className="text-gray-600">—</span>}
                    </td>
                    <td className="py-1.5 text-right tabular-nums border-b border-gray-700/40">
                      {pct !== null ? `${pct}%` : <span className="text-gray-600">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
