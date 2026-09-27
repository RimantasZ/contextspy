// Copyright 2026 Rimantas Zukaitis
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { LineageEdge, LineageGraph, LineageNode } from '../api/client'
import { useContextDiff } from '../api/hooks'
import { formatRequestDuration } from '../lib/format'
import { parseServerTimestamp } from './dashboard/dashboardFormat'

const NODE_W = 168
const NODE_H = 92
const LANE_H = 126
const LEFT = 72
const TOP = 40

type PositionedNode = LineageNode & { x: number; y: number }

function requestLabel(node: LineageNode): string {
  return node.external ? 'External parent' : `Request #${node.session_seq ?? '—'}`
}

function relationLabel(edge: LineageEdge): string {
  if (edge.certainty === 'exact') return 'Exact continuation'
  if (edge.certainty === 'inferred') return `Inferred ${Math.round((edge.confidence ?? 0) * 100)}%`
  return 'Suggested relation'
}

function stateLabel(state: LineageNode['parent_state']): string {
  const labels: Record<LineageNode['parent_state'], string> = {
    exact: 'Exact parent',
    inferred: 'Inferred parent',
    ambiguous: 'Ambiguous parent',
    root: 'No known parent',
    unresolved_exact: 'Missing exact parent',
    unavailable: 'Parent unavailable',
    external: 'Outside this session',
  }
  return labels[state]
}

function nodeTone(state: LineageNode['parent_state']): string {
  if (state === 'exact') return 'border-[var(--success)]'
  if (state === 'inferred') return 'border-[var(--accent)] border-dashed'
  if (state === 'ambiguous' || state === 'unresolved_exact') return 'border-[var(--warning)] border-dashed'
  return 'border-[var(--border-strong)]'
}

function layoutNodes(nodes: LineageNode[]): { nodes: PositionedNode[]; width: number; height: number } {
  if (nodes.length === 0) return { nodes: [], width: 820, height: 220 }
  const laneKeys = [...new Set(nodes.map((node) => `${node.lineage_number}:${node.branch}`))]
    .sort((a, b) => {
      const [al, ab] = a.split(':').map(Number)
      const [bl, bb] = b.split(':').map(Number)
      return al - bl || ab - bb
    })
  const laneByKey = new Map(laneKeys.map((key, index) => [key, index]))
  const starts = nodes.map((node) => new Date(node.started_at).getTime())
  const ends = nodes.map((node) => new Date(node.completed_at).getTime())
  const minTime = Math.min(...starts)
  const maxTime = Math.max(...ends, minTime + 1)
  const timelineWidth = Math.max(760, nodes.length * 120)
  const minDepth = Math.min(...nodes.map((node) => node.depth))
  const lastXByLane = new Map<number, number>()

  const positioned = [...nodes]
    .sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime() || a.request_id.localeCompare(b.request_id))
    .map((node) => {
      const lane = laneByKey.get(`${node.lineage_number}:${node.branch}`) ?? 0
      const timeRatio = (new Date(node.started_at).getTime() - minTime) / (maxTime - minTime)
      const timeX = LEFT + timeRatio * timelineWidth
      const depthX = LEFT + (node.depth - minDepth) * (NODE_W + 34)
      const previousX = lastXByLane.get(lane) ?? -Infinity
      const x = Math.max(timeX, depthX, previousX + NODE_W + 24)
      lastXByLane.set(lane, x)
      return { ...node, x, y: TOP + lane * LANE_H }
    })
  const width = Math.max(900, ...positioned.map((node) => node.x + NODE_W + 72))
  const height = TOP + laneKeys.length * LANE_H + 36
  return { nodes: positioned, width, height }
}

function DeltaStats({ edge }: { edge: LineageEdge }) {
  const summary = edge.delta?.summary
  if (!summary) return <p className="text-xs text-[var(--text-muted)]">Context delta unavailable.</p>
  return (
    <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
      {([
        ['Persisted', summary.persisted.blocks, summary.persisted.tokens],
        ['Promoted', summary.promoted.blocks, summary.promoted.tokens],
        ['Added', summary.added.blocks, summary.added.tokens],
        ['Removed', summary.removed.blocks, summary.removed.tokens],
      ] as const).map(([label, blocks, tokens]) => (
        <div key={label} className="rounded border border-[var(--border)] bg-[var(--surface-inset)] p-2">
          <dt className="text-[var(--text-muted)]">{label}</dt>
          <dd className="mt-0.5 font-semibold">{blocks} blocks</dd>
          <dd className="tabular-nums text-[var(--text-muted)]">{tokens.toLocaleString()} tokens</dd>
        </div>
      ))}
      <div className="rounded border border-[var(--border)] bg-[var(--surface-inset)] p-2">
        <dt className="text-[var(--text-muted)]">Replaced</dt>
        <dd className="mt-0.5 font-semibold">{summary.replaced.blocks} blocks</dd>
        <dd className="tabular-nums text-[var(--text-muted)]">{summary.replaced.tokens_before.toLocaleString()} → {summary.replaced.tokens_after.toLocaleString()}</dd>
      </div>
    </dl>
  )
}

export function SessionLineage({ graph }: { graph: LineageGraph }) {
  const navigate = useNavigate()
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null)
  const [selectedPathKey, setSelectedPathKey] = useState<string | null>(null)
  const [selectedPathRoot, setSelectedPathRoot] = useState<string | null>(null)
  const [pathSearch, setPathSearch] = useState('')
  const [windowOffset, setWindowOffset] = useState(0)
  const [pathNotice, setPathNotice] = useState('')
  const paths = useMemo(() => [...graph.lineage_paths].sort((a, b) => b.last_activity.localeCompare(a.last_activity) || b.key.localeCompare(a.key)), [graph.lineage_paths])
  const matchingPaths = paths.filter((path) => `${path.root_session_seq ?? ''} ${path.leaf_session_seq ?? ''} ${path.root_request_id} ${path.leaf_request_id}`.toLowerCase().includes(pathSearch.toLowerCase()))
  const selectedPath = paths.find((path) => path.key === selectedPathKey)
    ?? paths.find((path) => selectedNodeId && path.request_ids.includes(selectedNodeId))
    ?? paths.find((path) => path.root_request_id === selectedPathRoot)
    ?? paths[0]
  const selectedPathIndex = paths.findIndex((path) => path.key === selectedPath?.key)
  const pathLength = selectedPath?.request_ids.length ?? 0
  const windowEnd = Math.max(0, pathLength - Math.min(windowOffset, Math.max(0, pathLength - 1)))
  const windowStart = Math.max(0, windowEnd - 25)
  const visiblePathIds = selectedPath?.request_ids.slice(windowStart, windowEnd) ?? []
  const visibleIds = new Set(visiblePathIds)
  const firstId = visiblePathIds[0]
  const externalEdge = graph.edges.find((edge) => edge.target_request_id === firstId && edge.external_source)
  if (externalEdge) visibleIds.add(externalEdge.source_request_id)
  const visibleNodes = graph.nodes.filter((node) => visibleIds.has(node.request_id))
  const visibleEdges = graph.edges.filter((edge) => visibleIds.has(edge.source_request_id) && visibleIds.has(edge.target_request_id))
  const layout = useMemo(() => layoutNodes(visibleNodes), [graph.nodes, selectedPath?.key, windowOffset])
  const byId = useMemo(() => new Map(layout.nodes.map((node) => [node.request_id, node])), [layout.nodes])
  const allNodesById = useMemo(() => new Map(graph.nodes.map((node) => [node.request_id, node])), [graph.nodes])
  const parentByChild = useMemo(() => new Map(graph.edges
    .filter((edge) => edge.relation_type === 'context_continuation')
    .map((edge) => [edge.target_request_id, edge.source_request_id])), [graph.edges])
  const groupLabels = useMemo(() => new Map([
    ...graph.conversations.map((group) => [group.key, group.label] as const),
    ...(graph.auxiliary ? [[graph.auxiliary.key, graph.auxiliary.label] as const] : []),
  ]), [graph.conversations, graph.auxiliary])
  const membershipLabel = (node: LineageNode) => node.conversation_membership
    .map((item) => `${groupLabels.get(item.key) ?? item.key}${item.state === 'provisional_unassigned' ? ' (provisional)' : item.state === 'unassigned' ? ' (uncertain)' : ''}`)
    .join(', ')
  const selectedNode = selectedNodeId ? byId.get(selectedNodeId) ?? null : null
  const selectedAmbiguity = selectedNode
    ? graph.ambiguous_candidates.find((item) => item.request_id === selectedNode.request_id) ?? null
    : null
  const selectedEdge = selectedEdgeKey
    ? visibleEdges.find((edge) => `${edge.source_request_id}:${edge.target_request_id}:${edge.relation_type}` === selectedEdgeKey) ?? null
    : null
  const detailedDiff = useContextDiff(
    selectedEdge?.target_request_id ?? '',
    selectedEdge?.source_request_id ?? null,
  )
  const maxDuration = Math.max(1, ...visibleNodes.map((node) => node.duration_ms ?? 0))

  useEffect(() => {
    if (selectedPathKey && selectedPath?.key !== selectedPathKey) {
      setSelectedPathKey(selectedPath?.key ?? null)
      setWindowOffset(0)
      setPathNotice('Selected path changed during capture. Showing the nearest surviving path.')
    }
  }, [selectedPathKey, selectedPath?.key])

  useEffect(() => {
    if (selectedNodeId && !byId.has(selectedNodeId)) setSelectedNodeId(null)
    if (selectedEdgeKey && !selectedEdge) setSelectedEdgeKey(null)
  }, [byId, selectedEdge, selectedEdgeKey, selectedNodeId])

  function chooseNode(nodeId: string) {
    setSelectedNodeId(nodeId)
    setSelectedEdgeKey(null)
  }

  function chooseEdge(edge: LineageEdge) {
    setSelectedEdgeKey(`${edge.source_request_id}:${edge.target_request_id}:${edge.relation_type}`)
    setSelectedNodeId(null)
  }

  if (graph.nodes.length === 0) {
    return <div className="py-12 text-center text-sm text-[var(--text-muted)]">No invocations captured yet.</div>
  }

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="section-title">Lineage diagnostics</p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">{graph.conversation_count} supported conversation{graph.conversation_count === 1 ? '' : 's'} · {graph.auxiliary_request_count ?? 0} auxiliary request{(graph.auxiliary_request_count ?? 0) === 1 ? '' : 's'} · {graph.lineage_fragment_count} diagnostic lineage path{graph.lineage_fragment_count === 1 ? '' : 's'}. Unlinked paths are not automatically separate conversations. Request numbers show recording order.</p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs text-[var(--text-muted)]" aria-label="Lineage graph legend">
          <span><span className="mr-1 inline-block w-5 border-t-2 border-[var(--success)]" />Exact</span>
          <span><span className="mr-1 inline-block w-5 border-t-2 border-dashed border-[var(--accent)]" />Inferred</span>
          <span>+ / − added / removed blocks</span>
          <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[var(--warning)]" />Needs attention</span>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-[15rem_minmax(0,1fr)]">
        <div className="min-w-0">
          <label htmlFor="lineage-path-search" className="text-xs font-medium">Search diagnostic paths</label>
          <input id="lineage-path-search" className="app-field mt-1 w-full" value={pathSearch} onChange={(event) => setPathSearch(event.target.value)} placeholder="Root or leaf request" />
          <p role="status" aria-live="polite" className="mt-1 text-xs text-[var(--text-muted)]">{pathNotice}</p>
          <div className="mt-2 max-h-48 space-y-1 overflow-y-auto" aria-label="Diagnostic path index">
            {matchingPaths.slice(0, 50).map((path) => (
              <button key={path.key} type="button" aria-pressed={selectedPath?.key === path.key} className="app-button w-full text-left text-xs" onClick={() => { setSelectedPathKey(path.key); setSelectedPathRoot(path.root_request_id); setWindowOffset(0); setPathNotice('') }}>
                #{path.root_session_seq ?? '—'} → #{path.leaf_session_seq ?? '—'} · {path.request_count} requests<br />
                <span className="text-[var(--text-muted)]">{new Date(parseServerTimestamp(path.last_activity)).toLocaleString()} · starts: {stateLabel(path.start_parent_state)}</span>
              </button>
            ))}
            {matchingPaths.length === 0 && <p className="text-xs text-[var(--text-muted)]">No matching paths.</p>}
            {matchingPaths.length > 50 && <p className="text-xs text-[var(--text-muted)]">Showing 50 of {matchingPaths.length} matching paths. Search by root or leaf to narrow the list.</p>}
          </div>
        </div>
        <div className="min-w-0 text-xs text-[var(--text-muted)]">
          <p>{selectedPath ? `Showing path #${selectedPath.root_session_seq ?? '—'} → #${selectedPath.leaf_session_seq ?? '—'} · requests ${windowStart + 1}–${windowEnd} of ${selectedPath.request_count}` : 'No diagnostic path selected.'}</p>
          <div className="mt-2 flex gap-2">
            <button type="button" className="app-button" disabled={selectedPathIndex <= 0} onClick={() => { const path = paths[selectedPathIndex - 1]; setSelectedPathKey(path.key); setSelectedPathRoot(path.root_request_id); setWindowOffset(0) }}>Newer path</button>
            <button type="button" className="app-button" disabled={selectedPathIndex < 0 || selectedPathIndex >= paths.length - 1} onClick={() => { const path = paths[selectedPathIndex + 1]; setSelectedPathKey(path.key); setSelectedPathRoot(path.root_request_id); setWindowOffset(0) }}>Older path</button>
          </div>
          {windowStart > 0 && <button type="button" className="app-button mt-2" onClick={() => setWindowOffset((offset) => offset + 25)}>Earlier nodes</button>}
          {windowOffset > 0 && <button type="button" className="app-button mt-2 ml-2" onClick={() => setWindowOffset((offset) => Math.max(0, offset - 25))}>Newer nodes</button>}
        </div>
      </div>

      <div className="max-w-full overflow-auto rounded-lg border border-[var(--border)] bg-[var(--surface-inset)]">
        <div className="relative" style={{ width: layout.width, height: layout.height }}>
          <svg className="absolute inset-0" width={layout.width} height={layout.height} aria-label="Lineage graph">
            {visibleEdges.map((edge) => {
              const source = byId.get(edge.source_request_id)
              const target = byId.get(edge.target_request_id)
              if (!source || !target) return null
              const x1 = source.x + NODE_W
              const y1 = source.y + NODE_H / 2
              const x2 = target.x
              const y2 = target.y + NODE_H / 2
              const bend = Math.max(24, (x2 - x1) / 2)
              const selected = selectedEdge === edge
              const stroke = edge.certainty === 'exact' ? 'var(--success)' : 'var(--accent)'
              const summary = edge.delta?.summary
              return (
                <g key={`${edge.source_request_id}:${edge.target_request_id}:${edge.relation_type}`}>
                  <path
                    d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={selected ? 4 : 2}
                    strokeDasharray={edge.certainty === 'exact' ? undefined : '7 5'}
                    className="cursor-pointer"
                    role="button"
                    tabIndex={0}
                    aria-label={`${relationLabel(edge)} from ${requestLabel(source)} to ${requestLabel(target)}`}
                    onClick={() => chooseEdge(edge)}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') chooseEdge(edge) }}
                  />
                  <circle cx={x2} cy={y2} r="4" fill={stroke} />
                  {summary && (
                    <text
                      x={(x1 + x2) / 2}
                      y={(y1 + y2) / 2 - 7}
                      textAnchor="middle"
                      fill="var(--text-muted)"
                      fontSize="10"
                      className="pointer-events-none"
                    >
                      +{summary.added.blocks} / −{summary.removed.blocks}
                    </text>
                  )}
                </g>
              )
            })}
          </svg>

          {layout.nodes.map((node) => (
            <button
              key={node.request_id}
              type="button"
              onClick={() => chooseNode(node.request_id)}
              className={`absolute overflow-hidden rounded-lg border-2 bg-[var(--surface-elevated)] p-2 text-left shadow-sm transition-shadow hover:shadow-md ${nodeTone(node.parent_state)} ${selectedNodeId === node.request_id ? 'ring-2 ring-[var(--focus)]' : ''}`}
              style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
              aria-label={`${requestLabel(node)}, ${stateLabel(node.parent_state)}`}
            >
              <span className="flex items-center justify-between gap-2">
                <strong className="text-xs">{requestLabel(node)}</strong>
                {node.is_fork && <span className="app-badge px-1.5 py-0 text-[10px]" title={node.conversation_fork_status === 'confirmed' ? 'Confirmed conversation fork' : 'Graph branch · conversation split unconfirmed'}>{node.conversation_fork_status === 'confirmed' ? 'Confirmed fork' : 'Graph branch'}</span>}
              </span>
              <span className="mt-1 block truncate text-[11px] text-[var(--text-muted)]">{node.model ?? node.provider}</span>
              <span className="mt-1 flex justify-between text-[10px] text-[var(--text-muted)]">
                <span>{node.tokens_total_input.toLocaleString()} in</span>
                <span>{formatRequestDuration(node.duration_ms)}</span>
              </span>
              <span className="mt-1.5 block h-1.5 overflow-hidden rounded bg-[var(--surface-muted)]">
                <span className="block h-full rounded bg-[var(--accent)]" style={{ width: `${Math.max(4, ((node.duration_ms ?? 0) / maxDuration) * 100)}%` }} />
              </span>
              <span className="mt-1 block truncate text-[9px] uppercase tracking-wide text-[var(--text-subtle)]">{stateLabel(node.parent_state)}</span>
            </button>
          ))}
        </div>
      </div>

      {(selectedNode || selectedEdge) && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)] p-4">
          {selectedNode && (
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="section-title">{requestLabel(selectedNode)}</p>
                <p className="mt-1 text-sm text-[var(--text-muted)]">{membershipLabel(selectedNode) || 'External parent'} · request depth {selectedNode.depth}</p>
                {selectedNode.is_fork && <p className="mt-1 text-xs text-[var(--warning)]">{selectedNode.conversation_fork_status === 'confirmed' ? 'Confirmed conversation fork' : 'Graph branch · conversation split unconfirmed'}</p>}
                <p className="mt-2 text-xs text-[var(--text-muted)]">
                  Started {new Date(selectedNode.started_at).toLocaleString()}
                  {selectedNode.started_at_source !== 'observed' && ` (${selectedNode.started_at_source.replace('_', ' ')})`}
                </p>
                {selectedNode.parent_state === 'unresolved_exact' && selectedNode.predecessor_response_id && (
                  <p className="mt-2 text-xs text-[var(--warning)]">Provider parent {selectedNode.predecessor_response_id} was not captured.</p>
                )}
                {selectedAmbiguity && (
                  <div className="mt-3">
                    <p className="text-xs font-semibold text-[var(--warning)]">Ambiguous parent candidates</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {selectedAmbiguity.candidates.map((candidate) => (
                        <button
                          key={candidate.request_id}
                          type="button"
                          className="app-button min-h-8 py-1 text-xs"
                          onClick={() => navigate(`/requests/${candidate.request_id}`)}
                        >
                          {requestLabel(byId.get(candidate.request_id) ?? selectedNode)} · {Math.round(candidate.confidence * 100)}%
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <button type="button" className="app-button-primary" onClick={() => navigate(`/requests/${selectedNode.request_id}`)}>Open request</button>
            </div>
          )}
          {selectedEdge && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="section-title">{relationLabel(selectedEdge)}</p>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">{selectedEdge.evidence.reason_codes?.map((code) => code.replace(/_/g, ' ')).join(' · ')}</p>
                </div>
                <button type="button" className="app-button" onClick={() => navigate(`/requests/${selectedEdge.target_request_id}`)}>Open child request</button>
              </div>
              <DeltaStats edge={selectedEdge} />
              {detailedDiff.data && (
                <p className="text-xs text-[var(--text-muted)]">
                  Detailed mapping: {detailedDiff.data.delta.persisted.length} persisted, {detailedDiff.data.delta.promoted.length} promoted, {detailedDiff.data.delta.added.length} added, {detailedDiff.data.delta.removed.length} removed.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      <details className="rounded-lg border border-[var(--border)] bg-[var(--surface-elevated)]">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Lineage request list</summary>
        <div className="overflow-x-auto border-t border-[var(--border)]">
          <table className="w-full text-left text-xs">
            <thead><tr className="text-[var(--text-muted)]"><th className="p-2">Request</th><th className="p-2">Parent</th><th className="p-2">Relationship</th><th className="p-2">Conversation</th><th className="p-2">Context</th></tr></thead>
            <tbody>{visibleNodes.filter((node) => !node.external).map((node) => {
              const parent = parentByChild.get(node.request_id)
              const edge = parent ? graph.edges.find((item) => item.source_request_id === parent && item.target_request_id === node.request_id) : null
              return (
                <tr key={node.request_id} className="border-t border-[var(--border)]">
                  <td className="p-2"><button type="button" className="font-medium text-[var(--accent-soft-text)]" onClick={() => navigate(`/requests/${node.request_id}`)}>{requestLabel(node)}</button></td>
                  <td className="p-2">{parent ? (allNodesById.get(parent)?.session_seq != null ? `Request #${allNodesById.get(parent)?.session_seq}` : `External ${parent.slice(0, 8)}`) : '—'}</td>
                  <td className="p-2">{edge ? relationLabel(edge) : stateLabel(node.parent_state)}</td>
                  <td className="p-2">{membershipLabel(node) || '—'} · depth {node.depth}</td>
                  <td className="p-2 tabular-nums">{node.tokens_total_input.toLocaleString()} tokens</td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
      </details>
    </div>
  )
}
