import { ResponsiveContainer, Tooltip, Treemap } from 'recharts'
import type { ToolStat } from '../api/client'

export interface ToolTreemapLeaf {
  name: 'Definitions' | 'Results'
  toolName: string
  category: 'definition' | 'result'
  value: number
  total: number
  fill: string
}

export interface ToolTreemapNode {
  name: string
  value: number
  total: number
  fill: string
  groupedCount?: number
  children: ToolTreemapLeaf[]
}

function hashString(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function toolColor(toolName: string, variant: 'base' | 'definition' | 'result' = 'base'): string {
  if (toolName === 'Other') return variant === 'result' ? 'var(--tool-other-result)' : 'var(--tool-other-definition)'
  const hue = hashString(toolName) % 360
  return `hsl(${hue} var(--tool-color-saturation) var(--tool-fill-${variant}))`
}

export function buildToolTreemapData(tools: ToolStat[], threshold = 0.01): ToolTreemapNode[] {
  const positive = tools
    .map((tool) => ({ ...tool, total: Math.max(0, tool.definition_tokens) + Math.max(0, tool.result_tokens) }))
    .filter((tool) => tool.total > 0)
  const grandTotal = positive.reduce((sum, tool) => sum + tool.total, 0)
  const shown = positive.filter((tool) => tool.total / grandTotal >= threshold)
  const grouped = positive.filter((tool) => tool.total / grandTotal < threshold)

  const nodes = shown.map<ToolTreemapNode>((tool) => ({
    name: tool.tool_name,
    value: tool.total,
    total: tool.total,
    fill: toolColor(tool.tool_name),
    children: [
      ...(tool.definition_tokens > 0 ? [{ name: 'Definitions' as const, toolName: tool.tool_name, category: 'definition' as const, value: tool.definition_tokens, total: tool.total, fill: toolColor(tool.tool_name, 'definition') }] : []),
      ...(tool.result_tokens > 0 ? [{ name: 'Results' as const, toolName: tool.tool_name, category: 'result' as const, value: tool.result_tokens, total: tool.total, fill: toolColor(tool.tool_name, 'result') }] : []),
    ],
  }))

  if (grouped.length > 0) {
    const definition = grouped.reduce((sum, tool) => sum + tool.definition_tokens, 0)
    const result = grouped.reduce((sum, tool) => sum + tool.result_tokens, 0)
    const total = definition + result
    nodes.push({
      name: 'Other', value: total, total, fill: toolColor('Other'), groupedCount: grouped.length,
      children: [
        ...(definition > 0 ? [{ name: 'Definitions' as const, toolName: 'Other', category: 'definition' as const, value: definition, total, fill: toolColor('Other', 'definition') }] : []),
        ...(result > 0 ? [{ name: 'Results' as const, toolName: 'Other', category: 'result' as const, value: result, total, fill: toolColor('Other', 'result') }] : []),
      ],
    })
  }
  return nodes.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name))
}

interface ContentProps {
  x?: number
  y?: number
  width?: number
  height?: number
  depth?: number
  name?: string
  value?: number
  fill?: string
  toolName?: string
  category?: string
}

function TreemapContent({ x = 0, y = 0, width = 0, height = 0, depth = 0, name = '', value = 0, fill = 'var(--block-other)', toolName }: ContentProps) {
  if (depth === 0) return <g />
  const label = depth === 1 ? name : toolName
  return (
    <g>
      <rect x={x} y={y} width={Math.max(0, width)} height={Math.max(0, height)} fill={fill} stroke="var(--graphical-border)" strokeWidth={1} />
      {width > 74 && height > 31 && (
        <>
          <text x={x + 6} y={y + 15} fontSize={11} fontWeight={600} fill="var(--block-ink)">{String(label).slice(0, Math.floor(width / 7))}</text>
          {height > 46 && <text x={x + 6} y={y + 29} fontSize={9} fill="var(--block-ink)" opacity={0.72}>{depth > 1 ? name : value.toLocaleString()}</text>}
        </>
      )}
    </g>
  )
}

export function ToolTreemap({ tools }: { tools: ToolStat[] }) {
  const data = buildToolTreemapData(tools)
  const total = data.reduce((sum, node) => sum + node.total, 0)
  if (total === 0) return <div className="flex h-52 items-center justify-center text-sm italic text-[var(--text-muted)]">No tool usage recorded yet.</div>

  return (
    <div role="img" aria-label={`Tool token composition. ${data.map((node) => `${node.name}: ${node.total.toLocaleString()} tokens`).join('; ')}`}>
      <ResponsiveContainer width="100%" height={260}>
        <Treemap
          data={data}
          dataKey="value"
          nameKey="name"
          type="flat"
          isAnimationActive={false}
          content={<TreemapContent />}
        >
          <Tooltip
            contentStyle={{ backgroundColor: 'var(--chart-tooltip)', border: '1px solid var(--border)', borderRadius: '6px', color: 'var(--chart-tooltip-text)' }}
            formatter={(value: number, name: string, item: { payload?: { toolName?: string; groupedCount?: number } }) => [
              `${value.toLocaleString()} tokens (${((value / total) * 100).toFixed(1)}%)`,
              item.payload?.toolName ? `${item.payload.toolName} · ${name}` : name,
            ]}
          />
        </Treemap>
      </ResponsiveContainer>
      <p className="mt-1 text-[10px] text-[var(--text-muted)]">Tools below 1% of tool tokens are grouped as Other. Rectangle area is token-linear; shades distinguish definitions and results.</p>
    </div>
  )
}
