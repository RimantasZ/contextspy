import { useEffect, useMemo, useRef, useState } from 'react'
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

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

function JsonSearchText({ text, query }: { text: string; query: string }) {
  const matches = findTextMatches(text, query)
  if (matches.length === 0) return text
  const nodes: ReactNode[] = []
  let offset = 0
  matches.forEach((match, index) => {
    if (match.start > offset) nodes.push(text.slice(offset, match.start))
    nodes.push(
      <mark
        key={`${match.start}-${index}`}
        data-json-search-match
        className="rounded-[2px] bg-[var(--search-mark)] text-[var(--search-mark-text)]"
      >
        {text.slice(match.start, match.end)}
      </mark>,
    )
    offset = match.end
  })
  if (offset < text.length) nodes.push(text.slice(offset))
  return nodes
}

function jsonMatchCount(value: JsonValue, query: string): number {
  if (!query) return 0
  if (Array.isArray(value)) return value.reduce<number>((total, child) => total + jsonMatchCount(child, query), 0)
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).reduce<number>((total, [key, child]) => (
      total + findTextMatches(JSON.stringify(key), query).length + jsonMatchCount(child, query)
    ), 0)
  }
  return findTextMatches(JSON.stringify(value), query).length
}

function JsonScalar({ value, query }: { value: string | number | boolean | null; query: string }) {
  if (value === null) return <span className="text-[var(--text-muted)]"><JsonSearchText text="null" query={query} /></span>
  if (typeof value === 'boolean') return <span className="text-[var(--syntax-boolean)]"><JsonSearchText text={String(value)} query={query} /></span>
  if (typeof value === 'number') return <span className="text-[var(--syntax-number)]"><JsonSearchText text={String(value)} query={query} /></span>
  return <span className="text-[var(--syntax-string)]"><JsonSearchText text={JSON.stringify(value)} query={query} /></span>
}

function JsonKey({ name, query }: { name: string; query: string }) {
  return (
    <>
      <span className="text-[var(--syntax-key)]"><JsonSearchText text={JSON.stringify(name)} query={query} /></span>
      <span className="text-[var(--text-muted)]">: </span>
    </>
  )
}

function JsonTreeNode({ value, query, name, depth = 0, trailingComma = false }: {
  value: JsonValue
  query: string
  name?: string
  depth?: number
  trailingComma?: boolean
}) {
  const [collapsed, setCollapsed] = useState(depth > 1)
  const collection = Array.isArray(value) || (value !== null && typeof value === 'object')

  if (!collection) {
    return (
      <div className="min-w-0 break-words">
        {name != null && <JsonKey name={name} query={query} />}
        <JsonScalar value={value as string | number | boolean | null} query={query} />
        {trailingComma && <span className="text-[var(--text-subtle)]">,</span>}
      </div>
    )
  }

  const array = Array.isArray(value)
  const entries: Array<[string | undefined, JsonValue]> = array
    ? value.map((child) => [undefined, child])
    : Object.entries(value as { [key: string]: JsonValue })
  const open = array ? '[' : '{'
  const close = array ? ']' : '}'

  if (entries.length === 0) {
    return (
      <div>
        {name != null && <JsonKey name={name} query={query} />}
        <span className="text-[var(--text-muted)]">{open}{close}</span>
        {trailingComma && <span className="text-[var(--text-subtle)]">,</span>}
      </div>
    )
  }

  const searchActive = query.length > 0
  const shownCollapsed = collapsed && !searchActive
  const scopeName = name ?? (array ? 'array' : 'object')

  return (
    <div>
      <div className="flex min-w-0 items-start">
        <button
          type="button"
          aria-label={`${shownCollapsed ? 'Expand' : 'Collapse'} ${scopeName}`}
          aria-expanded={!shownCollapsed}
          disabled={searchActive}
          title={searchActive ? 'Clear search to collapse this scope' : `${shownCollapsed ? 'Expand' : 'Collapse'} ${scopeName}`}
          onClick={() => setCollapsed((current) => !current)}
          className="mr-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)] disabled:cursor-default disabled:opacity-50"
        >
          {shownCollapsed ? '▶' : '▼'}
        </button>
        <div className="min-w-0 break-words">
          {name != null && <JsonKey name={name} query={query} />}
          <span className="text-[var(--text-muted)]">{open}</span>
          {shownCollapsed && (
            <>
              <button
                type="button"
                onClick={() => setCollapsed(false)}
                className="mx-1 rounded px-1 text-[var(--text-subtle)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
              >
                {entries.length} {array ? `item${entries.length === 1 ? '' : 's'}` : `key${entries.length === 1 ? '' : 's'}`} …
              </button>
              <span className="text-[var(--text-muted)]">{close}</span>
              {trailingComma && <span className="text-[var(--text-subtle)]">,</span>}
            </>
          )}
        </div>
      </div>
      {!shownCollapsed && (
        <>
          <div className="ml-2.5 border-l border-[var(--border)] pl-3">
            {entries.map(([key, child], index) => (
              <JsonTreeNode
                key={key ?? index}
                value={child}
                name={key}
                depth={depth + 1}
                query={query}
                trailingComma={index < entries.length - 1}
              />
            ))}
          </div>
          <div className="pl-5 text-[var(--text-muted)]">
            {close}{trailingComma && <span className="text-[var(--text-subtle)]">,</span>}
          </div>
        </>
      )}
    </div>
  )
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
  const markRefs = useRef<Array<HTMLElement | null>>([])
  const jsonRootRef = useRef<HTMLDivElement>(null)
  const raw = content ?? ''
  const format = useMemo(() => formattedContent(raw), [raw])
  const parsedJson = useMemo<JsonValue | undefined>(() => {
    if (!format.canFormat) return undefined
    try { return JSON.parse(raw) as JsonValue } catch { return undefined }
  }, [format.canFormat, raw])
  const showJsonTree = pretty && parsedJson !== undefined
  const displayed = pretty && format.canFormat ? format.formatted : raw
  const matches = useMemo(() => findTextMatches(displayed, query), [displayed, query])
  const treeMatches = useMemo(() => parsedJson === undefined ? 0 : jsonMatchCount(parsedJson, query), [parsedJson, query])
  const matchCount = showJsonTree ? treeMatches : matches.length

  useEffect(() => {
    setActiveIndex(0)
    markRefs.current = []
  }, [displayed, query, showJsonTree])

  useEffect(() => {
    if (showJsonTree) {
      const marks = [...(jsonRootRef.current?.querySelectorAll<HTMLElement>('[data-json-search-match]') ?? [])]
      marks.forEach((mark, index) => mark.classList.toggle('search-match-active', index === activeIndex))
      marks[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
      return
    }
    markRefs.current[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
  }, [activeIndex, matchCount, showJsonTree])

  function moveMatch(delta: number) {
    if (matchCount === 0) return
    setActiveIndex((current) => (current + delta + matchCount) % matchCount)
  }

  return (
    <section className="surface-inset min-w-0 overflow-hidden rounded-md border border-[var(--border)]" aria-label={title}>
      <div className="space-y-2 border-b border-[var(--border)] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
          <div className="flex flex-wrap items-center gap-3">
            <label className={`flex items-center gap-1.5 text-xs ${format.canFormat ? 'cursor-pointer text-[var(--text-muted)]' : 'cursor-not-allowed text-[var(--text-subtle)]'}`}>
              <input
                type="checkbox"
                checked={format.canFormat && pretty}
                disabled={!format.canFormat}
                onChange={(event) => setPretty(event.target.checked)}
              />
              Format JSON
            </label>
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
            {query ? (matchCount > 0 ? `${activeIndex + 1} / ${matchCount}` : '0 / 0') : '—'}
          </span>
          <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matchCount === 0} onClick={() => moveMatch(-1)} aria-label="Previous occurrence">
            Previous
          </button>
          <button type="button" className="app-button min-h-8 py-1 text-xs" disabled={matchCount === 0} onClick={() => moveMatch(1)} aria-label="Next occurrence">
            Next
          </button>
        </div>
      </div>
      <div
        ref={jsonRootRef}
        className="min-h-28 min-w-0 overflow-auto p-4 font-mono text-xs leading-5 text-[var(--text)]"
        style={{ maxHeight }}
      >
        {content == null ? (
          <span className="italic text-[var(--text-muted)]">{emptyMessage}</span>
        ) : showJsonTree ? (
          <div role="region" aria-label="Formatted JSON">
            <JsonTreeNode value={parsedJson} query={query.toLocaleLowerCase()} />
          </div>
        ) : (
          <pre className="min-w-0 [overflow-wrap:anywhere] [white-space:pre-wrap]">
            <HighlightedText text={displayed} matches={matches} activeIndex={activeIndex} refs={markRefs} />
          </pre>
        )}
      </div>
    </section>
  )
}
