import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useSessionHotspots } from '../../api/hooks'
import type {
  BlockHotspotRow, FileHotspotRow, HotspotGroup, HotspotInContext, HotspotOccurrence, HotspotParams, HotspotSort,
  SessionHotspots, SourceHotspotRow,
} from '../../api/client'
import { BLOCK_VISUALS, FILTERABLE_BLOCK_TYPES, visualForType } from '../../lib/blockVisuals'
import { SegmentedControl } from '../ui/SegmentedControl'
import { CATEGORY_LABELS, CATEGORY_ORDER } from '../ContextBar'
import { barPercent, ESTIMATED_CHAR_WIDTH, ESTIMATED_TRACK_WIDTH, labelPlacement, sharePct } from './labelFit'
import type { LabelPlacement } from './labelFit'

const SCOPE_NOTES: Record<string, string> = {
  auxiliary_request: 'The latest request is auxiliary: showing the whole session.',
  conversation_unavailable: 'That conversation is not available: showing the whole session.',
}
const WHOLE_SESSION = '__session'
const GROUPS: HotspotGroup[] = ['block', 'source', 'file']
const SORTS: HotspotSort[] = ['total_tokens', 'occurrences']
const IN_CONTEXT: HotspotInContext[] = ['all', 'current', 'dropped']
/** URL parameters this view owns (the session view parameters stay untouched). */
export const HOTSPOT_URL_PARAMS = ['group', 'scope', 'conversation', 'sort', 'category', 'block_type', 'source', 'in_context', 'layout'] as const

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback
}

function seq(value: number | null): string {
  return value == null ? '—' : `#${value}`
}

type Row = BlockHotspotRow | SourceHotspotRow | FileHotspotRow
type Layout = 'name' | 'bar'

function Badge({ children, title, tone }: { children: string; title: string; tone?: 'warning' }) {
  return <span title={title} className={`app-badge ${tone === 'warning' ? 'status-warning' : ''}`}>{children}</span>
}

function TypeIcon({ blockType }: { blockType: string | null }) {
  const visual = BLOCK_VISUALS[visualForType(blockType)]
  return (
    <span aria-hidden title={visual.label} style={{ background: visual.color, borderColor: visual.border }}
      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border text-[10px] font-semibold text-white">
      {visual.short}
    </span>
  )
}

function ActivityIcon({ activity }: { activity: string | null }) {
  return (
    <span aria-hidden title={activity ?? 'no activity'}
      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-[var(--border)] bg-[var(--surface-muted)] text-[10px] font-semibold uppercase text-[var(--text-muted)]">
      {(activity ?? '·').slice(0, 1)}
    </span>
  )
}

/** The bar of a row; in the "on the bar" layout it also carries the label (inside when it fits, else after the bar). */
function BarCell({ pct, label }: { pct: number; label?: string }) {
  const trackRef = useRef<HTMLDivElement>(null)
  const labelRef = useRef<HTMLSpanElement>(null)
  const [placement, setPlacement] = useState<LabelPlacement>(() => labelPlacement((label?.length ?? 0) * ESTIMATED_CHAR_WIDTH, 0, 1))
  useLayoutEffect(() => {
    const track = trackRef.current
    const text = labelRef.current
    if (!track || !text) return
    const measure = () => {
      const trackWidth = track.clientWidth || ESTIMATED_TRACK_WIDTH  // no layout (tests): assume a typical track
      const textWidth = text.scrollWidth || (label?.length ?? 0) * ESTIMATED_CHAR_WIDTH
      setPlacement(labelPlacement(textWidth, (trackWidth * pct) / 100, trackWidth))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(track)
    return () => observer.disconnect()
  }, [label, pct])
  return (
    <div ref={trackRef} className="relative h-5 min-w-0 rounded-sm bg-[var(--surface-muted)]" aria-hidden={label ? undefined : true}>
      <div data-testid="hotspot-bar" className="h-full rounded-sm bg-[var(--accent)]" style={{ width: `${pct}%`, minWidth: pct > 0 ? 2 : 0 }} />
      {label && (
        <span ref={labelRef} data-placement={placement}
          className={`absolute inset-y-0 flex items-center truncate whitespace-nowrap px-1.5 text-xs ${placement === 'inside' ? 'text-[var(--text-on-accent)]' : 'text-[var(--text)]'}`}
          style={placement === 'inside' ? { left: 0, maxWidth: `${Math.max(pct, 0)}%` } : { left: `calc(${pct}% + 2px)`, right: 0 }}>
          {label}
        </span>
      )}
    </div>
  )
}

function Fact({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{name}</dt>
      <dd className="break-words text-xs text-[var(--text)]">{children}</dd>
    </div>
  )
}

function range(first: number | null, last: number | null): string {
  return `${seq(first)}–${seq(last)}`
}

interface RowView {
  key: string
  label: string
  title: string
  icon: React.ReactNode
  badges?: React.ReactNode
  openLabel: string
  open?: () => void
  facts: React.ReactNode
  preview?: string | null
  actions?: React.ReactNode
}

function describeRow(row: Row, group: HotspotGroup, onOpen: (at: HotspotOccurrence) => void, onFilter: (source: string) => void): RowView {
  if (group === 'source') {
    const r = row as SourceHotspotRow
    return {
      key: r.key, label: r.source_key, icon: <ActivityIcon activity={r.activity} />,
      title: `${r.source_key}\nLargest single block: ${r.largest.token_count.toLocaleString()} tokens`,
      openLabel: `Open the largest block of ${r.source_key}`,
      open: r.largest.request_id ? () => onOpen(r.largest) : undefined,
      facts: <>
        <Fact name="Activity">{r.activity ?? '—'}</Fact>
        <Fact name="Distinct blocks">{r.distinct_blocks.toLocaleString()}</Fact>
        <Fact name="Requests">{r.request_count.toLocaleString()}</Fact>
        <Fact name="Largest block">{r.largest.token_count.toLocaleString()} tokens</Fact>
      </>,
      actions: <button type="button" className="app-button-ghost min-h-7 px-2 py-0 text-xs" onClick={() => onFilter(r.source_key)}
        aria-label={`Show the blocks of ${r.source_key}`}>Blocks</button>,
    }
  }
  if (group === 'file') {
    const r = row as FileHotspotRow
    return {
      key: r.key, label: r.file_path, icon: <TypeIcon blockType="tool_result" />,
      title: `${r.file_path}\nTokens attributed to this file: read ${r.result_tokens.toLocaleString()}, written or edited ${r.call_tokens.toLocaleString()}`,
      badges: !r.in_latest_request ? <Badge tone="warning" title="Not in the latest request of this scope">dropped</Badge> : undefined,
      openLabel: `Open the latest request that touched ${r.file_path}`,
      open: r.latest.request_id ? () => onOpen(r.latest) : undefined,
      facts: <>
        <Fact name="Read">{r.result_tokens.toLocaleString()} tokens</Fact>
        <Fact name="Edited">{r.call_tokens.toLocaleString()} tokens</Fact>
        <Fact name="Versions">{r.distinct_versions.toLocaleString()}</Fact>
        <Fact name="Requests">{r.request_count.toLocaleString()}</Fact>
        <Fact name="Seen in">{range(r.first_seen_session_seq, r.last_seen_session_seq)}</Fact>
      </>,
    }
  }
  const r = row as BlockHotspotRow
  const label = r.label ?? r.block_type ?? 'Block'
  return {
    key: r.key, label, icon: <TypeIcon blockType={r.block_type} />,
    title: r.preview ? `${label}\n${r.preview}` : r.content_purged ? `${label}\n(content no longer stored)` : label,
    badges: !r.in_latest_request ? <Badge tone="warning" title="Not in the latest request of this scope">dropped</Badge> : undefined,
    openLabel: `Open the latest request carrying ${label}`,
    open: r.latest.request_id ? () => onOpen(r.latest) : undefined,
    facts: <>
      <Fact name="Requests">{r.request_count.toLocaleString()}</Fact>
      <Fact name="Tokens each">{r.tokens_per_occurrence != null ? r.tokens_per_occurrence.toLocaleString() : 'sizes differ'}</Fact>
      <Fact name="Seen in">{range(r.first_seen_session_seq, r.last_seen_session_seq)}</Fact>
      {r.run_count > 1 && <Fact name="Stretches">
        <Badge title={`Left the context and came back (${r.run_count} separate stretches)`}>reappears</Badge> {r.run_count.toLocaleString()}
      </Fact>}
      {r.block_types && <Fact name="Appears as">
        <Badge title={`The same content appears as ${r.block_types.join(', ')}`}>{`${r.block_types.length} types`}</Badge> {r.block_types.join(', ')}
      </Fact>}
      {r.category && <Fact name="Category">{CATEGORY_LABELS[r.category] ?? r.category}</Fact>}
      {r.source_key && <Fact name="Source">{r.source_key}</Fact>}
      {r.tool_name && <Fact name="Tool">{r.tool_name}</Fact>}
      {r.file_path && <Fact name="File">{r.file_path}</Fact>}
    </>,
    preview: r.preview ?? (r.content_purged ? '(content no longer stored)' : null),
  }
}

function metricParts(row: Row, sort: HotspotSort, occurrencesTotal: number) {
  const tokens = { value: row.total_tokens, pct: row.share_pct, name: 'Total tokens' }
  const occurrences = { value: row.occurrence_count, pct: sharePct(row.occurrence_count, occurrencesTotal), name: 'Occurrences' }
  return sort === 'total_tokens' ? { main: tokens, other: occurrences } : { main: occurrences, other: tokens }
}

function gridColumns(layout: Layout): string {
  return layout === 'name'
    ? '1rem 1.25rem minmax(0, 2fr) minmax(0, 3fr) 9.5rem'
    : '1rem 1.25rem minmax(0, 1fr) 9.5rem'
}

function HotspotRow({ row, group, sort, layout, largest, occurrencesTotal, expanded, onToggle, onOpen, onFilter }: {
  row: Row; group: HotspotGroup; sort: HotspotSort; layout: Layout; largest: number; occurrencesTotal: number
  expanded: boolean; onToggle: () => void; onOpen: (at: HotspotOccurrence) => void; onFilter: (source: string) => void
}) {
  const view = describeRow(row, group, onOpen, onFilter)
  const { main, other } = metricParts(row, sort, occurrencesTotal)
  return (
    <li className="border-b border-[var(--border)] last:border-b-0">
      <button type="button" aria-expanded={expanded} aria-label={`Show details of ${view.label}`} title={view.title} onClick={onToggle}
        className="grid w-full items-center gap-x-2 px-2 py-0.5 text-left hover:bg-[var(--surface-hover)]" style={{ gridTemplateColumns: gridColumns(layout) }}>
        <span aria-hidden className="text-[10px] text-[var(--text-muted)]">{expanded ? '▾' : '▸'}</span>
        {view.icon}
        {layout === 'name' && (
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm text-[var(--text)]">{view.label}</span>
            {view.badges}
          </span>
        )}
        {layout === 'name' ? <BarCell pct={barPercent(main.value, largest)} /> : (
          <span className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1"><BarCell pct={barPercent(main.value, largest)} label={view.label} /></span>
            {view.badges}
          </span>
        )}
        <span className="text-right text-sm tabular-nums">
          <span className="font-semibold">{main.value.toLocaleString()}</span>{' '}
          <span className="text-xs text-[var(--text-muted)]">({main.pct}%)</span>
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 bg-[var(--surface-muted)] px-3 py-2 pl-9">
          <dl className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-x-4 gap-y-1">
            <Fact name={other.name}>{other.value.toLocaleString()} ({other.pct}%)</Fact>
            {view.facts}
          </dl>
          {view.preview && <p className="break-words font-mono text-xs text-[var(--text-muted)]">{view.preview}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="app-button min-h-7 px-2 py-0 text-xs" disabled={!view.open} onClick={view.open} aria-label={view.openLabel}>
              {group === 'source' ? 'Open the largest block' : 'Open latest request'}
            </button>
            {view.actions}
          </div>
        </div>
      )}
    </li>
  )
}

function TableHeader({ group, sort, layout, onSort }: { group: HotspotGroup; sort: HotspotSort; layout: Layout; onSort: (value: HotspotSort) => void }) {
  const other: HotspotSort = sort === 'total_tokens' ? 'occurrences' : 'total_tokens'
  const noun = group === 'block' ? 'Block' : group === 'source' ? 'Source' : 'File'
  const metric = sort === 'total_tokens' ? 'Total tokens' : 'Occurrences'
  return (
    <div className="grid items-center gap-x-2 border-b border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]"
      style={{ gridTemplateColumns: gridColumns(layout) }}>
      <span aria-hidden /><span aria-hidden />
      <span>{noun}</span>
      {layout === 'name' && <span aria-hidden />}
      <button type="button" className="text-right uppercase tracking-wide hover:text-[var(--text)]" aria-label={`Sort by ${other === 'total_tokens' ? 'total tokens' : 'occurrences'} instead`}
        title={`Sorted by ${metric.toLowerCase()}; click to sort by ${other === 'total_tokens' ? 'total tokens' : 'occurrences'}`} onClick={() => onSort(other)}>
        {metric} ▾
      </button>
    </div>
  )
}

function Summary({ data }: { data: SessionHotspots }) {
  const { summary } = data
  const fidelity = Object.entries(summary.fidelity_counts).filter(([kind, count]) => kind !== 'complete' && count > 0)
  const noun = data.group === 'block' ? 'blocks' : data.group === 'source' ? 'sources' : 'files'
  return (
    <div className="space-y-1 text-xs text-[var(--text-muted)]">
      <p>
        {data.rows.length === 0 ? `No ${noun} here.` : <>
          Top {data.rows.length.toLocaleString()} of {data.total_rows.toLocaleString()} {noun} = <strong className="text-[var(--text)]">{summary.returned_share_pct}%</strong> of{' '}
          <span title="Sum of the visible tokens of every block in every request of the scope; not the provider's billed total">{summary.visible_tokens_total.toLocaleString()} visible tokens</span>
        </>}
        {' · '}{summary.scope_request_count.toLocaleString()} request{summary.scope_request_count === 1 ? '' : 's'}
        {fidelity.length > 0 && <> · <span title="Partial or opaque requests hide part of their context, so these totals count visible tokens only">{fidelity.map(([kind, count]) => `${count} ${kind}`).join(', ')}</span></>}
      </p>
      {summary.unidentifiable && summary.unidentifiable.blocks > 0 && (
        <p title="Hidden or empty content has no identity, so it cannot be matched across requests">
          {summary.unidentifiable.blocks.toLocaleString()} unidentifiable block{summary.unidentifiable.blocks === 1 ? '' : 's'}, {summary.unidentifiable.tokens.toLocaleString()} tokens, not listed.
        </p>
      )}
    </div>
  )
}

/** Hot spots of one session: blocks, sources or files ranked by the visible tokens they keep adding to the context. */
export function HotSpots({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const group = pick(searchParams.get('group'), GROUPS, 'block')
  const sort = pick(searchParams.get('sort'), SORTS, 'total_tokens')
  const conversation = searchParams.get('conversation')
  const scope = searchParams.get('scope') === 'session' && !conversation ? 'session' : 'conversation'
  const category = group === 'block' ? searchParams.get('category') : null
  const blockType = group === 'block' ? searchParams.get('block_type') : null
  const source = group === 'block' ? searchParams.get('source') : null
  const layout = pick<Layout>(searchParams.get('layout'), ['name', 'bar'], 'name')
  const inContext = group === 'block' ? pick(searchParams.get('in_context'), IN_CONTEXT, 'all') : 'all'

  const params = useMemo<Omit<HotspotParams, 'limit' | 'offset'>>(() => ({
    group, sort, scope, conversation: scope === 'conversation' ? conversation : null,
    category, block_type: blockType, source, in_context: inContext,
  }), [group, sort, scope, conversation, category, blockType, source, inContext])
  const query = useSessionHotspots(sessionId, params)

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const toggle = (key: string) => setExpanded((current) => {
    const next = new Set(current)
    if (!next.delete(key)) next.add(key)
    return next
  })

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams)
    for (const [name, value] of Object.entries(patch)) {
      if (value == null || value === '') next.delete(name)
      else next.set(name, value)
    }
    setSearchParams(next)
  }

  const first = query.data?.pages[0]
  const rows = (query.data?.pages ?? []).flatMap((page) => page.rows as (BlockHotspotRow | SourceHotspotRow | FileHotspotRow)[])
  const last = query.data?.pages[query.data.pages.length - 1]
  const merged = first && last ? ({ ...last, rows } as SessionHotspots) : undefined
  const selectedConversation = first?.conversations.find((c) => c.selected)?.key
  const scopeValue = scope === 'session' ? WHOLE_SESSION : conversation ?? selectedConversation ?? ''
  const open = (at: HotspotOccurrence) => { if (at.request_id) navigate(`/requests/${at.request_id}?block=${at.block_id}`) }

  return (
    <section aria-label="Hot spots" className="min-w-0 space-y-4">
      <div className="panel space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
            Scope
            <select aria-label="Scope" className="app-field min-h-8 py-1 text-xs" value={scopeValue}
              onChange={(event) => event.target.value === WHOLE_SESSION
                ? update({ scope: 'session', conversation: null })
                : update({ scope: null, conversation: event.target.value })}>
              {(first?.conversations ?? []).map((c) => (
                <option key={c.key} value={c.key}>{`${c.code ?? 'Conversation'} · ${c.request_count.toLocaleString()} requests`}</option>
              ))}
              <option value={WHOLE_SESSION}>Whole session</option>
            </select>
          </label>
          <SegmentedControl label="Group by" value={group}
            options={[{ value: 'block', label: 'Blocks' }, { value: 'source', label: 'Sources' }, { value: 'file', label: 'Files' }]}
            onChange={(value) => update({ group: value === 'block' ? null : value, category: null, block_type: null, source: null, in_context: null })} />
          <SegmentedControl label="Sort by" value={sort}
            options={[{ value: 'total_tokens', label: 'Total tokens' }, { value: 'occurrences', label: 'Occurrences' }]}
            onChange={(value) => update({ sort: value === 'total_tokens' ? null : value })} />
          <SegmentedControl label="Labels" value={layout}
            options={[{ value: 'name', label: 'Name column' }, { value: 'bar', label: 'On the bar' }]}
            onChange={(value) => update({ layout: value === 'name' ? null : value })} />
        </div>
        {group === 'block' && (
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]" title="The same categories as the context bar and donut">
              Category
              <select aria-label="Category" className="app-field min-h-8 py-1 text-xs" value={category ?? ''}
                onChange={(event) => update({ category: event.target.value || null })}>
                <option value="">All</option>
                {CATEGORY_ORDER.map((name) => <option key={name} value={name}>{CATEGORY_LABELS[name] ?? name}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
              Block type
              <select aria-label="Block type" className="app-field min-h-8 py-1 text-xs" value={blockType ?? ''}
                onChange={(event) => update({ block_type: event.target.value || null })}>
                <option value="">All</option>
                {FILTERABLE_BLOCK_TYPES.map((type) => <option key={type} value={type}>{BLOCK_VISUALS[visualForType(type)].label}</option>)}
              </select>
            </label>
            <SegmentedControl label="In context" value={inContext}
              options={[{ value: 'all', label: 'All' }, { value: 'current', label: 'Still in context' }, { value: 'dropped', label: 'Dropped' }]}
              onChange={(value) => update({ in_context: value === 'all' ? null : value })} />
            {source && (
              <button type="button" className="app-badge gap-1" onClick={() => update({ source: null })} aria-label={`Remove the source filter ${source}`}>
                Source: {source} <span aria-hidden>✕</span>
              </button>
            )}
          </div>
        )}
      </div>

      {query.isLoading && <p className="py-8 text-center text-sm text-[var(--text-muted)]">Analysing the context…</p>}
      {query.isError && !merged && <p role="alert" className="py-8 text-center text-sm text-[var(--danger)]">Hot spots could not be loaded.</p>}

      {merged && first && (
        <div className={query.isFetching && !query.isFetchingNextPage ? 'opacity-60' : undefined}>
          {first.scope_note && <p className="mb-2 text-xs text-[var(--text-muted)]">{SCOPE_NOTES[first.scope_note] ?? first.scope_note}</p>}
          <Summary data={merged} />
          {rows.length > 0 && (
            <div className="surface mt-3 overflow-hidden rounded-lg border border-[var(--border)]">
              <TableHeader group={merged.group} sort={sort} layout={layout} onSort={(value) => update({ sort: value === 'total_tokens' ? null : value })} />
              <ul>
                {rows.map((row) => (
                  <HotspotRow key={row.key} row={row} group={merged.group} sort={sort} layout={layout}
                    largest={sort === 'total_tokens' ? rows[0].total_tokens : rows[0].occurrence_count}
                    occurrencesTotal={merged.summary.occurrences_total} expanded={expanded.has(row.key)} onToggle={() => toggle(row.key)} onOpen={open}
                    onFilter={(value) => update({ group: null, source: value, category: null, block_type: null, in_context: null })} />
                ))}
              </ul>
            </div>
          )}
          {query.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <button type="button" className="app-button" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
              </button>
            </div>
          )}
          {!query.hasNextPage && last?.has_more && <p className="mt-3 text-center text-xs text-[var(--text-muted)]">Showing the top {rows.length.toLocaleString()}; narrow the scope or filters to see the rest.</p>}
        </div>
      )}
    </section>
  )
}
