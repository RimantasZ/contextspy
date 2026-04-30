import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import type { ToolStat } from '../api/client'

const PALETTE = [
  '#818cf8', '#34d399', '#f59e0b', '#f87171', '#38bdf8', '#a78bfa',
  '#fb923c', '#4ade80', '#e879f9', '#facc15', '#2dd4bf', '#f472b6',
]
const color = (i: number) => PALETTE[i % PALETTE.length]

// Shared vertical legend, multi-column at >20 / >40 tools
function ToolLegend({ tools }: { tools: ToolStat[] }) {
  const cols = tools.length > 40 ? 3 : tools.length > 20 ? 2 : 1
  const perCol = Math.ceil(tools.length / cols)
  const columns: { tool: ToolStat; idx: number }[][] = Array.from({ length: cols }, (_, c) =>
    tools.slice(c * perCol, (c + 1) * perCol).map((tool, r) => ({ tool, idx: c * perCol + r }))
  )
  return (
    <div className="flex gap-3 shrink-0 pr-3 border-r border-gray-700">
      {columns.map((col, ci) => (
        <div key={ci} className="flex flex-col gap-1">
          {col.map(({ tool, idx }) => (
            <div key={tool.tool_name} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color(idx) }} />
              <span
                className="text-xs text-gray-300 truncate max-w-[120px]"
                title={tool.tool_name}
              >
                {tool.tool_name.length > 18 ? tool.tool_name.slice(0, 16) + '…' : tool.tool_name}
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
    <div className="flex flex-col items-center min-w-[140px]">
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-1">{title}</p>
      {total === 0 ? (
        <div className="flex items-center justify-center h-[150px] w-full text-gray-500 text-xs italic border border-gray-700 rounded-lg px-2 text-center">
          {placeholder ?? `No ${title.toLowerCase()}`}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={150}>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              innerRadius={40}
              outerRadius={65}
              paddingAngle={2}
            >
              {data.map(entry => (
                <Cell key={entry.name} fill={color(entry.idx)} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
              formatter={(val: number, name: string) => [
                `${val.toLocaleString()} (${total > 0 ? ((val / total) * 100).toFixed(1) : 0}%)`,
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

interface Props {
  tools: ToolStat[]
  totalInputTokens?: number
}

export function ToolBreakdown({ tools, totalInputTokens }: Props) {
  const totalDef = tools.reduce((s, t) => s + t.definition_tokens, 0)
  const totalRes = tools.reduce((s, t) => s + t.result_tokens, 0)

  if (totalDef === 0 && totalRes === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 text-center text-gray-500 text-sm italic">
        No tool usage recorded yet.
      </div>
    )
  }

  // Stable colour index per tool (same across both charts and table)
  const defData = tools.map((t, i) => ({ name: t.tool_name, value: t.definition_tokens, idx: i })).filter(d => d.value > 0)
  const resData = tools.map((t, i) => ({ name: t.tool_name, value: t.result_tokens, idx: i })).filter(d => d.value > 0)

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-sm font-medium text-gray-300 mb-4">Tool token breakdown</p>
      <div className="flex gap-4">

        {/* ── Left: legend + pie charts ── */}
        <div className="flex items-center gap-3 shrink-0">
          <ToolLegend tools={tools} />
          <div className="flex gap-4">
            <Donut title="Tool definitions" data={defData} total={totalDef} />
            <Donut title="Tool results" data={resData} total={totalRes} placeholder="No tool results" />
          </div>
        </div>

        {/* Divider */}
        <div className="w-px bg-gray-700 self-stretch mx-1" />

        {/* ── Right: table ── */}
        <div className="flex-1 min-w-0 overflow-auto max-h-[260px]">
          <table className="w-full text-xs text-gray-300 border-separate border-spacing-0">
            <thead className="sticky top-0 bg-gray-800 z-10">
              <tr className="text-gray-500 uppercase tracking-wide">
                <th className="text-left pb-2 pr-3 font-medium border-b border-gray-700">Tool</th>
                <th className="text-right pb-2 pr-3 font-medium border-b border-gray-700 whitespace-nowrap">Def tokens</th>
                <th className="text-right pb-2 pr-3 font-medium border-b border-gray-700 whitespace-nowrap">Result tokens</th>
                <th className="text-right pb-2 font-medium border-b border-gray-700 whitespace-nowrap">% of context</th>
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
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color(i) }} />
                        <span className="truncate max-w-[160px]" title={t.tool_name}>{t.tool_name}</span>
                      </div>
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums border-b border-gray-700/40">
                      {t.definition_tokens.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums border-b border-gray-700/40">
                      {t.result_tokens > 0
                        ? t.result_tokens.toLocaleString()
                        : <span className="text-gray-600">—</span>}
                    </td>
                    <td className="py-1.5 text-right tabular-nums border-b border-gray-700/40">
                      {pct !== null
                        ? `${pct}%`
                        : <span className="text-gray-600">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

      </div>
    </div>
  )
}
