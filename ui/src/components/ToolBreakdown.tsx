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
              contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
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

// ── Panel 2: table ───────────────────────────────────────────────────────────
export function ToolBreakdownTable({ tools, totalInputTokens }: { tools: ToolStat[]; totalInputTokens?: number }) {
  const totalDef = tools.reduce((s, t) => s + t.definition_tokens, 0)
  const totalRes = tools.reduce((s, t) => s + t.result_tokens, 0)

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
                <th className="text-left pb-2 pr-3 font-medium border-b border-gray-700">Tool</th>
                <th className="text-right pb-2 pr-3 font-medium border-b border-gray-700 whitespace-nowrap">Def tokens</th>
                <th className="text-right pb-2 pr-3 font-medium border-b border-gray-700 whitespace-nowrap">Result tokens</th>
                <th className="text-right pb-2 font-medium border-b border-gray-700 whitespace-nowrap">% context</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t, i) => {
                const combined = t.definition_tokens + t.result_tokens
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
