import { useEffect, useMemo, useRef, useState } from 'react'
import type { MutableRefObject, ReactNode } from 'react'
import { tokenizeApi } from '../../api/client'
import { buildStructuredLines, findTextMatches, formattedContent, syntaxSegments } from '../../lib/searchableContent'
import type { ContentLanguage, StructuredLine, SyntaxKind, TextMatch } from '../../lib/searchableContent'

type ContentMode = 'verbatim' | 'formatted' | 'tokens' | 'structured'

const TOKEN_COLORS = Array.from({ length: 10 }, (_, index) => `var(--token-highlight-${index + 1})`)

const SYNTAX_CLASSES: Record<SyntaxKind, string> = {
  plain: '',
  key: 'text-[var(--syntax-key)]',
  string: 'text-[var(--syntax-string)]',
  number: 'text-[var(--syntax-number)]',
  boolean: 'text-[var(--syntax-boolean)]',
  keyword: 'font-medium text-[var(--syntax-key)]',
  comment: 'italic text-[var(--text-subtle)]',
}

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
        data-structured-search-match
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

function JsonTreeNode({ value, query, name, trailingComma = false, collapseAll, collapseRevision }: {
  value: JsonValue
  query: string
  name?: string
  trailingComma?: boolean
  collapseAll: boolean
  collapseRevision: number
}) {
  const [collapsed, setCollapsed] = useState(false)
  const collection = Array.isArray(value) || (value !== null && typeof value === 'object')

  useEffect(() => { setCollapsed(collapseAll) }, [collapseAll, collapseRevision])

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
                query={query}
                trailingComma={index < entries.length - 1}
                collapseAll={collapseAll}
                collapseRevision={collapseRevision}
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

function SyntaxHighlightedLine({ text, language, query }: {
  text: string
  language: ContentLanguage
  query: string
}) {
  const segments = syntaxSegments(text, language)
  const matches = findTextMatches(text, query)

  function renderRange(start: number, end: number, key: string): ReactNode[] {
    return segments.flatMap((segment, index) => {
      const segmentStart = Math.max(start, segment.start)
      const segmentEnd = Math.min(end, segment.end)
      if (segmentEnd <= segmentStart) return []
      return (
        <span key={`${key}-${index}-${segmentStart}`} className={SYNTAX_CLASSES[segment.kind]}>
          {text.slice(segmentStart, segmentEnd)}
        </span>
      )
    })
  }

  if (matches.length === 0) return renderRange(0, text.length, 'plain')
  const nodes: ReactNode[] = []
  let offset = 0
  matches.forEach((match, index) => {
    if (match.start > offset) nodes.push(...renderRange(offset, match.start, `before-${index}`))
    nodes.push(
      <mark
        key={`match-${match.start}-${index}`}
        data-structured-search-match
        className="rounded-[2px] bg-[var(--search-mark)] text-[var(--search-mark-text)]"
      >
        {renderRange(match.start, match.end, `match-${index}`)}
      </mark>,
    )
    offset = match.end
  })
  if (offset < text.length) nodes.push(...renderRange(offset, text.length, 'after'))
  return nodes
}

function descendantLineCount(node: StructuredLine): number {
  return node.children.reduce((total, child) => total + 1 + descendantLineCount(child), 0)
}

function StructuredLineNodeView({ node, language, query, collapseAll, collapseRevision }: {
  node: StructuredLine
  language: ContentLanguage
  query: string
  collapseAll: boolean
  collapseRevision: number
}) {
  const [collapsed, setCollapsed] = useState(false)
  const foldable = node.children.length > 0
  const searchActive = query.length > 0
  const shownCollapsed = foldable && collapsed && !searchActive
  const text = node.text.trimStart()

  useEffect(() => { setCollapsed(collapseAll) }, [collapseAll, collapseRevision])

  return (
    <div>
      <div className="flex min-w-0 items-start">
        {foldable ? (
          <button
            type="button"
            aria-label={`${shownCollapsed ? 'Expand' : 'Collapse'} line ${node.id + 1}`}
            aria-expanded={!shownCollapsed}
            disabled={searchActive}
            title={searchActive ? 'Clear search to collapse this scope' : `${shownCollapsed ? 'Expand' : 'Collapse'} scope`}
            onClick={() => setCollapsed((current) => !current)}
            className="mr-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)] disabled:cursor-default disabled:opacity-50"
          >
            {shownCollapsed ? '▶' : '▼'}
          </button>
        ) : <span className="mr-1 h-5 w-5 shrink-0" aria-hidden="true" />}
        <code className="min-w-0 break-words">
          {text ? <SyntaxHighlightedLine text={text} language={language} query={query} /> : '\u00a0'}
          {shownCollapsed && (
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              className="ml-2 rounded px-1 text-[var(--text-subtle)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
            >
              {descendantLineCount(node)} lines …
            </button>
          )}
        </code>
      </div>
      {foldable && !shownCollapsed && (
        <div className="ml-2.5 border-l border-[var(--border)] pl-3">
          {node.children.map((child) => (
            <StructuredLineNodeView
              key={child.id}
              node={child}
              language={language}
              query={query}
              collapseAll={collapseAll}
              collapseRevision={collapseRevision}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function StructuredTextView({ content, language, languageLabel, query, collapseAll, collapseRevision }: {
  content: string
  language: ContentLanguage
  languageLabel: string
  query: string
  collapseAll: boolean
  collapseRevision: number
}) {
  const lines = useMemo(() => buildStructuredLines(content, language), [content, language])
  return (
    <div role="region" aria-label={`Structured ${languageLabel}`}>
      {lines.map((line) => (
        <StructuredLineNodeView
          key={line.id}
          node={line}
          language={language}
          query={query}
          collapseAll={collapseAll}
          collapseRevision={collapseRevision}
        />
      ))}
    </div>
  )
}

function TokenHighlightedText({ text, tokens, windowStart, matches, activeIndex, refs }: {
  text: string
  tokens: string[]
  windowStart: number
  matches: TextMatch[]
  activeIndex: number
  refs: MutableRefObject<Array<HTMLElement | null>>
}) {
  const joined = tokens.join('')
  const tokenRanges = text.slice(windowStart).startsWith(joined)
    ? tokens.reduce<Array<{ start: number; end: number; color: string }>>((ranges, token, index) => {
      const start = ranges[ranges.length - 1]?.end ?? windowStart
      ranges.push({ start, end: start + token.length, color: TOKEN_COLORS[index % TOKEN_COLORS.length] })
      return ranges
    }, [])
    : []
  let tokenCursor = 0

  function renderRange(start: number, end: number, key: string): ReactNode[] {
    const nodes: ReactNode[] = []
    let offset = start
    while (tokenCursor < tokenRanges.length && tokenRanges[tokenCursor].end <= start) tokenCursor += 1
    let index = tokenCursor
    while (index < tokenRanges.length && tokenRanges[index].start < end) {
      const range = tokenRanges[index]
      const rangeStart = Math.max(start, range.start)
      const rangeEnd = Math.min(end, range.end)
      if (rangeEnd <= rangeStart) { index += 1; continue }
      if (rangeStart > offset) nodes.push(<span key={`${key}-gap-${index}`}>{text.slice(offset, rangeStart)}</span>)
      nodes.push(
        <span key={`${key}-token-${index}-${rangeStart}`} className="rounded-[2px]" style={{ background: range.color }}>
          {text.slice(rangeStart, rangeEnd)}
        </span>,
      )
      offset = rangeEnd
      if (range.end <= end) index += 1
      else break
    }
    tokenCursor = index
    if (offset < end) nodes.push(<span key={`${key}-rest`}>{text.slice(offset, end)}</span>)
    return nodes
  }

  if (matches.length === 0) return renderRange(0, text.length, 'plain')
  const nodes: ReactNode[] = []
  let offset = 0
  matches.forEach((match, index) => {
    if (match.start > offset) nodes.push(...renderRange(offset, match.start, `before-${index}`))
    nodes.push(
      <mark
        key={`match-${match.start}-${index}`}
        ref={(node) => { refs.current[index] = node }}
        className={`rounded-[2px] bg-[var(--search-mark)] text-[var(--search-mark-text)] ${index === activeIndex ? 'search-match-active' : ''}`}
      >
        {renderRange(match.start, match.end, `match-${index}`)}
      </mark>,
    )
    offset = match.end
  })
  if (offset < text.length) nodes.push(...renderRange(offset, text.length, 'after'))
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
  const [mode, setMode] = useState<ContentMode>('structured')
  const [tokenWindowStart, setTokenWindowStart] = useState(0)
  const [tokenResult, setTokenResult] = useState<{ text: string; start: number; tokens: string[] } | null>(null)
  const [tokenizing, setTokenizing] = useState(false)
  const [tokenError, setTokenError] = useState(false)
  const [collapseCommand, setCollapseCommand] = useState({ collapsed: false, revision: 0 })
  const markRefs = useRef<Array<HTMLElement | null>>([])
  const contentRootRef = useRef<HTMLDivElement>(null)
  const raw = content ?? ''
  const format = useMemo(() => formattedContent(raw), [raw])
  const parsedJson = useMemo<JsonValue | undefined>(() => {
    if (format.language !== 'json') return undefined
    try { return JSON.parse(raw) as JsonValue } catch { return undefined }
  }, [format.language, raw])
  const showJsonTree = mode === 'structured' && parsedJson !== undefined
  const showStructuredText = mode === 'structured' && parsedJson === undefined
  const showStructured = showJsonTree || showStructuredText
  const displayed = mode === 'verbatim' ? raw : format.formatted
  const matches = useMemo(() => findTextMatches(displayed, query), [displayed, query])
  const treeMatches = useMemo(() => parsedJson === undefined ? 0 : jsonMatchCount(parsedJson, query), [parsedJson, query])
  const structuredTextMatches = useMemo(() => showStructuredText
    ? format.formatted.split('\n').reduce((total, line) => total + findTextMatches(line.trimStart(), query).length, 0)
    : 0, [format.formatted, query, showStructuredText])
  const matchCount = showJsonTree ? treeMatches : showStructuredText ? structuredTextMatches : matches.length
  const activeTokens = tokenResult?.text === displayed && tokenResult.start === tokenWindowStart ? tokenResult.tokens : []
  const tokenizedWindow = activeTokens.join('')
  const tokenWindowEnd = tokenWindowStart + tokenizedWindow.length
  const tokensTruncated = mode === 'tokens' && !tokenizing && tokenizedWindow.length > 0
    && displayed.slice(tokenWindowStart).startsWith(tokenizedWindow)
    && (tokenWindowStart > 0 || tokenWindowEnd < displayed.length)

  useEffect(() => {
    if (mode !== 'tokens' || content == null
      || (tokenResult?.text === displayed && tokenResult.start === tokenWindowStart)) return
    let active = true
    setTokenizing(true)
    setTokenError(false)
    tokenizeApi.tokenize([displayed.slice(tokenWindowStart)])
      .then((result) => {
        if (active) {
          setTokenResult({ text: displayed, start: tokenWindowStart, tokens: result.results[0] ?? [] })
          setTokenizing(false)
        }
      })
      .catch(() => {
        if (active) {
          setTokenResult({ text: displayed, start: tokenWindowStart, tokens: [] })
          setTokenError(true)
          setTokenizing(false)
        }
      })
    return () => { active = false }
  }, [content, displayed, mode, tokenResult?.start, tokenResult?.text, tokenWindowStart])

  useEffect(() => { setTokenWindowStart(0) }, [displayed])

  useEffect(() => {
    setActiveIndex(0)
    markRefs.current = []
  }, [displayed, mode, query])

  useEffect(() => {
    if (showStructured) {
      const marks = [...(contentRootRef.current?.querySelectorAll<HTMLElement>('[data-structured-search-match]') ?? [])]
      marks.forEach((mark, index) => mark.classList.toggle('search-match-active', index === activeIndex))
      marks[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
      return
    }
    markRefs.current[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
  }, [activeIndex, matchCount, showStructured])

  function moveMatch(delta: number) {
    if (matchCount === 0) return
    setActiveIndex((current) => (current + delta + matchCount) % matchCount)
  }

  function moveTokenWindowToCurrentView() {
    const viewport = contentRootRef.current
    if (!viewport || displayed.length === 0) return
    const viewportCenter = viewport.scrollTop + viewport.clientHeight / 2
    const scrollRatio = viewport.scrollHeight > 0 ? viewportCenter / viewport.scrollHeight : 0
    const targetOffset = Math.round(Math.max(0, Math.min(1, scrollRatio)) * displayed.length)
    const estimatedWindowLength = Math.max(tokenizedWindow.length, Math.min(50_000, displayed.length))
    const centeredStart = targetOffset - Math.floor(estimatedWindowLength / 2)
    setTokenWindowStart(Math.max(0, Math.min(displayed.length - 1, centeredStart)))
  }

  function toggleAllScopes() {
    setCollapseCommand((current) => ({ collapsed: !current.collapsed, revision: current.revision + 1 }))
  }

  return (
    <section className="surface-inset min-w-0 overflow-hidden rounded-md border border-[var(--border)]" aria-label={title}>
      <div className="space-y-2 border-b border-[var(--border)] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
              Formatting
              <select
                value={mode}
                disabled={content == null}
                onChange={(event) => setMode(event.target.value as ContentMode)}
                className="app-field py-1.5"
              >
                <option value="verbatim">Verbatim raw</option>
                <option value="formatted">Formatted raw</option>
                <option value="tokens">Highlight tokens</option>
                <option value="structured">Structured view</option>
              </select>
            </label>
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-subtle)]" title={`Detected content type: ${format.languageLabel}`}>{format.languageLabel}</span>
            {showStructured && (
              <button
                type="button"
                className="app-button min-h-8 py-1 text-xs"
                disabled={query.length > 0}
                title={query.length > 0 ? 'Clear search to change all scopes' : undefined}
                onClick={toggleAllScopes}
              >
                {collapseCommand.collapsed ? 'Expand all' : 'Collapse all'}
              </button>
            )}
            {mode === 'tokens' && tokenizing && <span className="text-[10px] text-[var(--text-muted)]" role="status">Tokenizing…</span>}
            {mode === 'tokens' && tokenError && <span className="text-[10px] text-[var(--danger)]" role="status">Token highlighting unavailable</span>}
            {tokensTruncated && (
              <button
                type="button"
                className="text-[10px] text-[var(--accent)] underline decoration-dotted underline-offset-2 hover:text-[var(--accent-hover)]"
                title="Token highlighting is capped for large content. Move the highlighted window to the content currently in view."
                onClick={moveTokenWindowToCurrentView}
              >
                {tokenWindowStart === 0 ? 'First ' : ''}{activeTokens.length.toLocaleString()} {activeTokens.length === 1 ? 'token' : 'tokens'} highlighted · Move to current view
              </button>
            )}
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
        ref={contentRootRef}
        data-content-viewport
        className="min-h-28 min-w-0 overflow-auto p-4 font-mono text-xs leading-5 text-[var(--text)]"
        style={{ maxHeight }}
      >
        {content == null ? (
          <span className="italic text-[var(--text-muted)]">{emptyMessage}</span>
        ) : showJsonTree ? (
          <div role="region" aria-label="Structured JSON">
            <JsonTreeNode
              value={parsedJson}
              query={query.toLocaleLowerCase()}
              collapseAll={collapseCommand.collapsed}
              collapseRevision={collapseCommand.revision}
            />
          </div>
        ) : showStructuredText ? (
          <StructuredTextView
            content={format.formatted}
            language={format.language}
            languageLabel={format.languageLabel}
            query={query.toLocaleLowerCase()}
            collapseAll={collapseCommand.collapsed}
            collapseRevision={collapseCommand.revision}
          />
        ) : mode === 'tokens' ? (
          <pre className="min-w-0 [overflow-wrap:anywhere] [white-space:pre-wrap]">
            <TokenHighlightedText
              text={displayed}
              tokens={activeTokens}
              windowStart={tokenWindowStart}
              matches={matches}
              activeIndex={activeIndex}
              refs={markRefs}
            />
          </pre>
        ) : (
          <pre className="min-w-0 [overflow-wrap:anywhere] [white-space:pre-wrap]">
            <HighlightedText text={displayed} matches={matches} activeIndex={activeIndex} refs={markRefs} />
          </pre>
        )}
      </div>
    </section>
  )
}
