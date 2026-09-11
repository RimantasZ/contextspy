import { useCallback, useMemo, useRef, useState } from 'react'
import { analyzeContent, buildStructuredLines } from '../../lib/content'
import { ContentToolbar } from './content-viewer/ContentToolbar'
import { JsonTreeView, countJsonMatches, jsonCollapsiblePaths } from './content-viewer/JsonTreeView'
import { PlainTextView } from './content-viewer/PlainTextView'
import { StructuredTextView, countStructuredMatches, structuredCollapsiblePaths } from './content-viewer/StructuredTextView'
import { TokenHighlightView } from './content-viewer/TokenHighlightView'
import type { ContentMode, JsonValue } from './content-viewer/types'
import { useContentSearch } from './content-viewer/useContentSearch'
import { useTokenWindow } from './content-viewer/useTokenWindow'
import { useTreeExpansion } from './content-viewer/useTreeExpansion'

export function SearchableContentViewer({ title, content, emptyMessage = 'No content was captured.', maxHeight = 520 }: {
  title: string
  content: string | null | undefined
  emptyMessage?: string
  maxHeight?: number
}) {
  const [mode, setMode] = useState<ContentMode>('structured')
  const contentRootRef = useRef<HTMLDivElement>(null)
  const raw = content ?? ''
  const analysis = useMemo(() => analyzeContent(raw), [raw])
  const parsedJson = useMemo<JsonValue | undefined>(() => {
    if (analysis.language !== 'json') return undefined
    try { return JSON.parse(raw) as JsonValue } catch { return undefined }
  }, [analysis.language, raw])
  const structuredLines = useMemo(() => buildStructuredLines(raw, analysis.language), [analysis.language, raw])
  const showJsonTree = mode === 'structured' && parsedJson !== undefined
  const showStructuredText = mode === 'structured' && parsedJson === undefined
  const showStructured = showJsonTree || showStructuredText
  const displayed = mode === 'verbatim' ? raw : analysis.formatted
  const collapsiblePaths = useMemo(() => showJsonTree && parsedJson !== undefined
    ? jsonCollapsiblePaths(parsedJson)
    : showStructuredText ? structuredCollapsiblePaths(structuredLines) : [],
  [parsedJson, showJsonTree, showStructuredText, structuredLines])
  const expansion = useTreeExpansion(collapsiblePaths, `${analysis.language}:${raw}`)
  const countTreeMatches = useCallback((query: string) => showJsonTree && parsedJson !== undefined
    ? countJsonMatches(parsedJson, query)
    : showStructuredText ? countStructuredMatches(structuredLines, query) : 0,
  [parsedJson, showJsonTree, showStructuredText, structuredLines])
  const search = useContentSearch({ text: displayed, modeKey: mode, structured: showStructured, countStructuredMatches: countTreeMatches, contentRootRef })
  const tokens = useTokenWindow(displayed, mode === 'tokens' && content != null)

  function toggleAllScopes() {
    if (expansion.hasCollapsedScopes) expansion.expandAll()
    else expansion.collapseBelowDepth(2)
  }

  return (
    <section className="surface-inset min-w-0 overflow-hidden rounded-md border border-[var(--border)]" aria-label={title}>
      <ContentToolbar
        title={title} hasContent={content != null} mode={mode} languageLabel={analysis.languageLabel}
        showStructured={showStructured && collapsiblePaths.length > 0} hasCollapsedScopes={expansion.hasCollapsedScopes}
        query={search.query} matchCount={search.matchCount} activeIndex={search.activeIndex}
        tokenizing={tokens.loading} tokenError={tokens.error} tokenCount={tokens.tokenCount}
        tokensTruncated={mode === 'tokens' && tokens.truncated}
        tokenWindowAtStart={(tokens.result?.window_start ?? tokens.requestedOffset) === 0}
        onMode={setMode} onToggleScopes={toggleAllScopes}
        onMoveTokenWindow={() => tokens.moveToViewport(contentRootRef.current)}
        onCopy={() => navigator.clipboard?.writeText(displayed)} onQuery={search.setQuery} onMoveMatch={search.moveMatch}
      />
      <div ref={contentRootRef} data-content-viewport className="min-h-28 min-w-0 overflow-auto p-4 font-mono text-xs leading-5 text-[var(--text)]" style={{ maxHeight }}>
        {content == null ? <span className="italic text-[var(--text-muted)]">{emptyMessage}</span>
          : showJsonTree && parsedJson !== undefined ? <JsonTreeView value={parsedJson} query={search.query} collapsedPaths={expansion.collapsedPaths} onToggle={expansion.toggle} />
            : showStructuredText ? <StructuredTextView nodes={structuredLines} language={analysis.language} languageLabel={analysis.languageLabel} query={search.query} collapsedPaths={expansion.collapsedPaths} onToggle={expansion.toggle} />
              : mode === 'tokens' ? <TokenHighlightView text={displayed} window={tokens.result} matches={search.matches} activeIndex={search.activeIndex} markRefs={search.markRefs} />
                : <PlainTextView text={displayed} matches={search.matches} activeIndex={search.activeIndex} markRefs={search.markRefs} />}
      </div>
    </section>
  )
}
