import type { ContentMode } from './types'

export function ContentToolbar({
  title, hasContent, mode, languageLabel, showStructured, hasCollapsedScopes, query, matchCount,
  activeIndex, tokenizing, tokenError, tokenCount, tokensTruncated, tokenWindowAtStart,
  onMode, onToggleScopes, onMoveTokenWindow, onCopy, onQuery, onMoveMatch,
}: {
  title: string
  hasContent: boolean
  mode: ContentMode
  languageLabel: string
  showStructured: boolean
  hasCollapsedScopes: boolean
  query: string
  matchCount: number
  activeIndex: number
  tokenizing: boolean
  tokenError: boolean
  tokenCount: number
  tokensTruncated: boolean
  tokenWindowAtStart: boolean
  onMode: (mode: ContentMode) => void
  onToggleScopes: () => void
  onMoveTokenWindow: () => void
  onCopy: () => void
  onQuery: (query: string) => void
  onMoveMatch: (delta: number) => void
}) {
  return (
    <div className="space-y-2 border-b border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">Formatting<select aria-label="Formatting" value={mode} disabled={!hasContent} onChange={(event) => onMode(event.target.value as ContentMode)} className="app-field py-1.5"><option value="verbatim">Verbatim raw</option><option value="formatted">Formatted raw</option><option value="tokens">Highlight tokens</option><option value="structured">Structured view</option></select></label>
          <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-subtle)]" title={`Detected content type: ${languageLabel}`}>{languageLabel}</span>
          {showStructured && <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={query.length > 0} title={query ? 'Clear search to change all scopes' : undefined} onClick={onToggleScopes}>{hasCollapsedScopes ? 'Expand' : 'Collapse'}</button>}
          {mode === 'tokens' && tokenizing && <span className="text-[10px] text-[var(--text-muted)]" role="status">Tokenizing…</span>}
          {mode === 'tokens' && tokenError && <span className="text-[10px] text-[var(--danger)]" role="status">Token highlighting unavailable</span>}
          {tokensTruncated && <button type="button" className="text-[10px] text-[var(--accent)] underline decoration-dotted underline-offset-2 hover:text-[var(--accent-hover)]" title="Token highlighting is capped for large content. Move the highlighted window to the content currently in view." onClick={onMoveTokenWindow}>{tokenWindowAtStart ? 'First ' : ''}{tokenCount.toLocaleString()} {tokenCount === 1 ? 'token' : 'tokens'} highlighted · Move to current view</button>}
          {hasContent && <button type="button" className="app-button min-h-8 py-1 text-xs" onClick={onCopy}>Copy</button>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="min-w-[180px] flex-1"><span className="sr-only">Search {title.toLocaleLowerCase()}</span><input type="search" value={query} disabled={!hasContent} onChange={(event) => onQuery(event.target.value)} placeholder="Find in content…" className="app-field w-full py-1.5 text-xs" aria-label={`Search ${title.toLocaleLowerCase()}`} /></label>
        <span className="w-16 text-center text-xs tabular-nums text-[var(--text-muted)]" aria-live="polite">{query ? (matchCount > 0 ? `${activeIndex + 1} / ${matchCount}` : '0 / 0') : '—'}</span>
        <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matchCount === 0} onClick={() => onMoveMatch(-1)} aria-label="Previous occurrence">Previous</button>
        <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matchCount === 0} onClick={() => onMoveMatch(1)} aria-label="Next occurrence">Next</button>
      </div>
    </div>
  )
}
