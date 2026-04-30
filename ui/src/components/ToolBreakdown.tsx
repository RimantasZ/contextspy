import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import type { ToolStat } from '../api/client'

// Distinct colour palette — cycles if > 12 tools
const PALETTE = [
  '#818cf8', '#34d399', '#f59e0b', '#f87171', '#38bdf8', '#a78bfa',
  '#fb923c', '#4ade80', '#e879f9', '#facc15', '#2dd4bf', '#f472b6',
]

interface PaneProps {
  title: string
  data: { name: string; value: number }[]
  total: number
}

function DonutPane({ title, data, total }: PaneProps) {
  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 text-gray-500 text-sm italic">
        No {title.toLowerCase()}
      </div>
    )
  }
  return (
    <div>
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2 text-center">{title}</p>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={85}
            paddingAngle={2}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 12 }}
            formatter={(val: number, name: string) => [
              `${val.toLocaleString()} tokens (${total > 0 ? ((val / total) * 100).toFixed(1) : 0}%)`,
              name,
            ]}
          />
          <Legend
            iconSize={8}
            iconType="circle"
            wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            formatter={(value: string) =>
              value.length > 22 ? value.slice(0, 20) + '…' : value
            }
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}

interface Props {
  tools: ToolStat[]
}

export function ToolBreakdown({ tools }: Props) {
  const defData = tools
    .filter(t => t.definition_tokens > 0)
    .map(t => ({ name: t.tool_name, value: t.definition_tokens }))

  const resData = tools
    .filter(t => t.result_tokens > 0)
    .map(t => ({ name: t.tool_name, value: t.result_tokens }))

  const totalDef = defData.reduce((s, d) => s + d.value, 0)
  const totalRes = resData.reduce((s, d) => s + d.value, 0)
  const hasResults = totalRes > 0

  if (totalDef === 0 && totalRes === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 text-center text-gray-500 text-sm italic">
        No tool usage recorded yet.
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-sm font-medium text-gray-300 mb-4">Tool token breakdown</p>
      <div className={`grid gap-6 ${hasResults ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1 max-w-sm mx-auto'}`}>
        <DonutPane title="Tool definitions" data={defData} total={totalDef} />
        {hasResults ? (
          <DonutPane title="Tool results" data={resData} total={totalRes} />
        ) : (
          <div className="flex flex-col items-center justify-center h-48 text-gray-500 text-sm italic border border-gray-700 rounded-lg">
            No tool results in context
          </div>
        )}
      </div>
    </div>
  )
}
