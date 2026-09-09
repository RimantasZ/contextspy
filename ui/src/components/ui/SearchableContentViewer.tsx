import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { MutableRefObject, ReactNode } from 'react'
import { findTextMatches, formattedContent } from '../../lib/searchableContent'
import type { TextMatch } from '../../lib/searchableContent'

function HighlightedText({ text, matches, activeIndex, refs }: {
  text: string
  matches: TextMatch[]
  activeIndex: number
  refs: MutableRefObject<Array<HTMLElement | null>>
}) {
  if (matches.length === 0) return text
  const nodes: ReactNode[] = []
  let offset = 0
  matches.forEach((match, index) => {
    if (match.start > offset) nodes.push(text.slice(offset, match.start))
    nodes.push(
      <mark
        key={`${match.start}-${index}`}
        ref={(node) => { refs.current[index] = node }}
        className={`rounded-[2px] bg-[var(--search-mark)] text-[var(--search-mark-text)] ${index === activeIndex ? 'search-match-active' : ''}`}
      >
        {text.slice(match.start, match.end)}
      </mark>,
    )
    offset = match.end
  })
  if (offset < text.length) nodes.push(text.slice(offset))
  return nodes
}

export function SearchableContentViewer({
  title,
  content,
  emptyMessage = 'No content was captured.',
  maxHeight = 520,
}: {
  title: string
  content: string | null | undefined
  emptyMessage?: string
  maxHeight?: number
}) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [pretty, setPretty] = useState(true)
  const formatterId = useId()
  const markRefs = useRef<Array<HTMLElement | null>>([])
  const raw = content ?? ''
  const format = useMemo(() => formattedContent(raw), [raw])
  const displayed = pretty && format.canFormat ? format.formatted : raw
  const matches = useMemo(() => findTextMatches(displayed, query), [displayed, query])

  useEffect(() => {
    setActiveIndex(0)
    markRefs.current = []
  }, [displayed, query])

  useEffect(() => {
    markRefs.current[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
  }, [activeIndex, matches.length])

  function moveMatch(delta: number) {
    if (matches.length === 0) return
    setActiveIndex((current) => (current + delta + matches.length) % matches.length)
  }

  return (
    <section className="surface-inset min-w-0 overflow-hidden rounded-md border border-[var(--border)]" aria-label={title}>
      <div className="space-y-2 border-b border-[var(--border)] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
          <div className="flex flex-wrap items-center gap-3">
            <div className={`flex items-center gap-1.5 text-xs ${format.canFormat ? 'text-[var(--text-muted)]' : 'text-[var(--text-subtle)]'}`}>
              <input
                id={formatterId}
                type="checkbox"
                checked={format.canFormat && pretty}
                disabled={!format.canFormat}
                onChange={(event) => setPretty(event.target.checked)}
              />
              <label htmlFor={formatterId} className={format.canFormat ? 'cursor-pointer' : 'cursor-not-allowed'}>Pretty print JSON</label>
              <span className="min-w-14 text-[10px] font-medium uppercase tracking-wide text-[var(--text-subtle)]">
                {format.canFormat ? (pretty ? 'Formatted' : 'Raw') : 'Plain text'}
              </span>
            </div>
            {content != null && (
              <button type="button" className="app-button min-h-8 py-1 text-xs" onClick={() => navigator.clipboard?.writeText(displayed)}>
                Copy
              </button>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="min-w-[180px] flex-1">
            <span className="sr-only">Search {title.toLocaleLowerCase()}</span>
            <input
              type="search"
              value={query}
              disabled={content == null}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find in content…"
              className="app-field w-full py-1.5 text-xs"
              aria-label={`Search ${title.toLocaleLowerCase()}`}
            />
          </label>
          <span className="w-16 text-center text-xs tabular-nums text-[var(--text-muted)]" aria-live="polite">
            {query ? (matches.length > 0 ? `${activeIndex + 1} / ${matches.length}` : '0 / 0') : '—'}
          </span>
          <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matches.length === 0} onClick={() => moveMatch(-1)} aria-label="Previous occurrence">
            Previous
          </button>
          <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matches.length === 0} onClick={() => moveMatch(1)} aria-label="Next occurrence">
            Next
          </button>
        </div>
      </div>
      <pre
        className="min-h-28 min-w-0 overflow-auto p-4 font-mono text-xs leading-5 text-[var(--text)] [overflow-wrap:anywhere] [white-space:pre-wrap]"
        style={{ maxHeight }}
      >
        {content == null ? (
          <span className="italic text-[var(--text-muted)]">{emptyMessage}</span>
        ) : (
          <HighlightedText text={displayed} matches={matches} activeIndex={activeIndex} refs={markRefs} />
        )}
      </pre>
    </section>
  )
}
