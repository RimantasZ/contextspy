import { useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
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
  selectedKey?: string | null
  onSelect?: (selection: ToolTreemapSelection) => void
}

export interface ToolTreemapSelection {
  key: string
  label: string
  value: number
}

export function TreemapContent({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
  depth = 0,
  name = '',
  value = 0,
  fill = 'var(--block-other)',
  toolName,
  category,
  selectedKey,
  onSelect,
}: ContentProps) {
  if (depth === 0) return <g />
  if (depth === 1) return <g />

  const label = toolName
  const resolvedToolName = toolName ?? name
  const resolvedCategory = category === 'definition' ? 'definition' : 'result'
  const key = `${resolvedToolName}:${resolvedCategory}`
  const detailLabel = `${resolvedToolName} · ${name}`
  const selection: ToolTreemapSelection = {
    key,
    label: detailLabel,
    value,
  }
  const separatorPath = [
    x > 0 ? `M ${x} ${y} V ${y + height}` : '',
    y > 0 ? `M ${x} ${y} H ${x + width}` : '',
  ].filter(Boolean).join(' ')

  function onKeyDown(event: KeyboardEvent<SVGGElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect?.(selection)
  }

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${detailLabel}, ${value.toLocaleString()} tokens`}
      aria-pressed={selectedKey === key}
      onClick={() => onSelect?.(selection)}
      onKeyDown={onKeyDown}
      className="cursor-pointer outline-none"
    >
      <rect
        x={x}
        y={y}
        width={Math.max(0, width)}
        height={Math.max(0, height)}
        fill={fill}
        opacity={selectedKey && selectedKey !== key ? 0.62 : 1}
        shapeRendering="crispEdges"
      />
      {separatorPath && (
        <path
          d={separatorPath}
          fill="none"
          stroke="var(--graphical-border)"
          strokeWidth={1}
          shapeRendering="crispEdges"
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
        />
      )}
      {width > 74 && height > 31 && (
        <>
          <text x={x + 6} y={y + 15} fontSize={11} fontWeight={600} fill="var(--block-ink)">{String(label).slice(0, Math.floor(width / 7))}</text>
          {height > 46 && <text x={x + 6} y={y + 29} fontSize={9} fill="var(--block-ink)" opacity={0.72}>{name}</text>}
        </>
      )}
    </g>
  )
}

export function ToolTreemapSelectionDetails({ selected }: { selected: ToolTreemapSelection | null }) {
  if (!selected) return null
  return (
    <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-xs" role="status">
      <strong className="min-w-0 break-all text-[var(--text)]">{selected.label}</strong>
      <span className="shrink-0 tabular-nums text-[var(--text-muted)]">{selected.value.toLocaleString()} tokens</span>
    </div>
  )
}

export function ToolTreemap({ tools }: { tools: ToolStat[] }) {
  const data = useMemo(() => buildToolTreemapData(tools), [tools])
  const total = data.reduce((sum, node) => sum + node.total, 0)
  const [selected, setSelected] = useState<ToolTreemapSelection | null>(null)
  if (total === 0) return <div className="flex h-52 items-center justify-center text-sm italic text-[var(--text-muted)]">No tool usage recorded yet.</div>

  return (
    <div role="group" aria-label={`Tool token composition. ${data.map((node) => `${node.name}: ${node.total.toLocaleString()} tokens`).join('; ')}`}>
      <div className="border border-[var(--graphical-border)]">
        <ResponsiveContainer width="100%" height={260}>
          <Treemap
            data={data}
            dataKey="value"
            nameKey="name"
            type="flat"
            isAnimationActive={false}
            content={<TreemapContent selectedKey={selected?.key} onSelect={setSelected} />}
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
      </div>
      <ToolTreemapSelectionDetails selected={selected} />
      <p className="mt-1 text-[10px] text-[var(--text-muted)]">Tools below 1% of tool tokens are grouped as Other. Rectangle area is token-linear; shades distinguish definitions and results.</p>
    </div>
  )
}
