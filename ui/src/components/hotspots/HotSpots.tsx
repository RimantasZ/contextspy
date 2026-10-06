import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useSessionHotspots } from '../../api/hooks'
import type {
  BlockHotspotRow, FileHotspotRow, HotspotGroup, HotspotInContext, HotspotOccurrence, HotspotParams, HotspotSort,
  SessionHotspots, SourceHotspotRow,
} from '../../api/client'
import { BLOCK_VISUALS, FILTERABLE_BLOCK_TYPES, visualForType } from '../../lib/blockVisuals'
import { SegmentedControl } from '../ui/SegmentedControl'

const SCOPE_NOTES: Record<string, string> = {
  auxiliary_request: 'The latest request is auxiliary: showing the whole session.',
  conversation_unavailable: 'That conversation is not available: showing the whole session.',
}
const WHOLE_SESSION = '__session'
const GROUPS: HotspotGroup[] = ['block', 'source', 'file']
const SORTS: HotspotSort[] = ['total_tokens', 'occurrences']
const IN_CONTEXT: HotspotInContext[] = ['all', 'current', 'dropped']
/** URL parameters this view owns (the session view parameters stay untouched). */
export const HOTSPOT_URL_PARAMS = ['group', 'scope', 'conversation', 'sort', 'block_type', 'source', 'in_context'] as const

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback
}

function seq(value: number | null): string {
  return value == null ? '—' : `#${value}`
}

function ShareBar({ pct }: { pct: number }) {
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded bg-[var(--surface-muted)]" aria-hidden>
      <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${Math.min(100, Math.max(pct, 0.5))}%` }} />
    </div>
  )
}

function Badge({ children, title, tone }: { children: string; title: string; tone?: 'warning' }) {
  return <span title={title} className={`app-badge ${tone === 'warning' ? 'status-warning' : ''}`}>{children}</span>
}

function RowShell({ label, title, meta, badges, tokens, share, onOpen, openLabel, icon, actions }: {
  label: string; title?: string; meta: string; badges?: React.ReactNode; tokens: number; share: number
  onOpen?: () => void; openLabel: string; icon: React.ReactNode; actions?: React.ReactNode
}) {
  return (
    <li className="flex items-stretch gap-1 border-b border-[var(--border)] last:border-b-0">
      <button type="button" disabled={!onOpen} onClick={onOpen} aria-label={openLabel} title={title}
        className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left hover:bg-[var(--surface-hover)] disabled:cursor-default disabled:hover:bg-transparent">
        {icon}
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--text)]">{label}</span>
            {badges}
          </span>
          <span className="block truncate text-xs text-[var(--text-muted)]">{meta}</span>
        </span>
        <span className="w-28 shrink-0 text-right">
          <span className="block text-sm font-semibold tabular-nums">{tokens.toLocaleString()}</span>
          <span className="block text-[10px] text-[var(--text-muted)]">{share}% of visible tokens</span>
          <ShareBar pct={share} />
        </span>
      </button>
      {actions}
    </li>
  )
}

function TypeIcon({ blockType }: { blockType: string | null }) {
  const visual = BLOCK_VISUALS[visualForType(blockType)]
  return (
    <span aria-hidden title={visual.label} style={{ background: visual.color, borderColor: visual.border }}
      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded border text-[10px] font-semibold text-white">
      {visual.short}
    </span>
  )
}

function ActivityIcon({ activity }: { activity: string | null }) {
  return (
    <span aria-hidden title={activity ?? 'no activity'}
      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded border border-[var(--border)] bg-[var(--surface-muted)] text-[10px] font-semibold uppercase text-[var(--text-muted)]">
      {(activity ?? '·').slice(0, 1)}
    </span>
  )
}

function BlockRow({ row, onOpen }: { row: BlockHotspotRow; onOpen: (at: HotspotOccurrence) => void }) {
  const each = row.tokens_per_occurrence != null ? ` · ${row.tokens_per_occurrence.toLocaleString()} tokens each` : ' · sizes differ'
  const label = row.label ?? row.block_type ?? 'Block'
  return (
    <RowShell
      label={label}
      title={row.preview ? `${label}\n${row.preview}` : row.content_purged ? `${label}\n(content no longer stored)` : label}
      meta={`×${row.occurrence_count.toLocaleString()} · ${row.request_count.toLocaleString()} request${row.request_count === 1 ? '' : 's'}${each} · ${seq(row.first_seen_session_seq)}–${seq(row.last_seen_session_seq)}${row.preview ? ` · ${row.preview}` : ''}`}
      badges={<>
        {row.run_count > 1 && <Badge title={`Left the context and came back (${row.run_count} separate stretches)`}>reappears</Badge>}
        {!row.in_latest_request && <Badge tone="warning" title="Not in the latest request of this scope">dropped</Badge>}
        {row.block_types && <Badge title={`The same content appears as ${row.block_types.join(', ')}`}>{`${row.block_types.length} types`}</Badge>}
      </>}
      tokens={row.total_tokens} share={row.share_pct} icon={<TypeIcon blockType={row.block_type} />}
      onOpen={row.latest.request_id ? () => onOpen(row.latest) : undefined}
      openLabel={`Open the latest request carrying ${label}`}
    />
  )
}

function SourceRow({ row, onOpen, onFilter }: {
  row: SourceHotspotRow; onOpen: (at: HotspotOccurrence) => void; onFilter: (source: string) => void
}) {
  return (
    <RowShell
      label={row.source_key}
      title={`Open the largest single block of ${row.source_key} (${row.largest.token_count.toLocaleString()} tokens)`}
      meta={`${row.activity ? `${row.activity} · ` : ''}${row.distinct_blocks.toLocaleString()} distinct block${row.distinct_blocks === 1 ? '' : 's'} · ×${row.occurrence_count.toLocaleString()} · ${row.request_count.toLocaleString()} request${row.request_count === 1 ? '' : 's'}`}
      tokens={row.total_tokens} share={row.share_pct} icon={<ActivityIcon activity={row.activity} />}
      onOpen={row.largest.request_id ? () => onOpen(row.largest) : undefined}
      openLabel={`Open the largest block of ${row.source_key}`}
      actions={<button type="button" className="app-button-ghost my-1 mr-2 min-h-7 shrink-0 px-2 py-0 text-xs" onClick={() => onFilter(row.source_key)}
        aria-label={`Show the blocks of ${row.source_key}`}>Blocks</button>}
    />
  )
}

function FileRow({ row, onOpen }: { row: FileHotspotRow; onOpen: (at: HotspotOccurrence) => void }) {
  return (
    <RowShell
      label={row.file_path}
      title={`${row.file_path}\nTokens attributed to this file: read ${row.result_tokens.toLocaleString()}, written or edited ${row.call_tokens.toLocaleString()}`}
      meta={`read ${row.result_tokens.toLocaleString()} · edited ${row.call_tokens.toLocaleString()} · ${row.distinct_versions.toLocaleString()} version${row.distinct_versions === 1 ? '' : 's'} · ×${row.occurrence_count.toLocaleString()} · ${row.request_count.toLocaleString()} request${row.request_count === 1 ? '' : 's'}`}
      badges={!row.in_latest_request ? <Badge tone="warning" title="Not in the latest request of this scope">dropped</Badge> : undefined}
      tokens={row.total_tokens} share={row.share_pct} icon={<TypeIcon blockType="tool_result" />}
      onOpen={row.latest.request_id ? () => onOpen(row.latest) : undefined}
      openLabel={`Open the latest request that touched ${row.file_path}`}
    />
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
  const blockType = group === 'block' ? searchParams.get('block_type') : null
  const source = group === 'block' ? searchParams.get('source') : null
  const inContext = group === 'block' ? pick(searchParams.get('in_context'), IN_CONTEXT, 'all') : 'all'

  const params = useMemo<Omit<HotspotParams, 'limit' | 'offset'>>(() => ({
    group, sort, scope, conversation: scope === 'conversation' ? conversation : null,
    block_type: blockType, source, in_context: inContext,
  }), [group, sort, scope, conversation, blockType, source, inContext])
  const query = useSessionHotspots(sessionId, params)

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
            onChange={(value) => update({ group: value === 'block' ? null : value, block_type: null, source: null, in_context: null })} />
          <SegmentedControl label="Sort by" value={sort}
            options={[{ value: 'total_tokens', label: 'Total tokens' }, { value: 'occurrences', label: 'Occurrences' }]}
            onChange={(value) => update({ sort: value === 'total_tokens' ? null : value })} />
        </div>
        {group === 'block' && (
          <div className="flex flex-wrap items-center gap-3">
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
            <ul className="surface mt-3 overflow-hidden rounded-lg border border-[var(--border)]">
              {merged.group === 'block' && merged.rows.map((row) => <BlockRow key={row.key} row={row} onOpen={open} />)}
              {merged.group === 'source' && merged.rows.map((row) => <SourceRow key={row.key} row={row} onOpen={open}
                onFilter={(value) => update({ group: null, source: value, block_type: null, in_context: null })} />)}
              {merged.group === 'file' && merged.rows.map((row) => <FileRow key={row.key} row={row} onOpen={open} />)}
            </ul>
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
