import type { ContentLanguage, StructuredNode, SyntaxKind } from '../../../lib/content'
import { syntaxSegments } from '../../../lib/content'
import { findTextMatches } from '../../../lib/textRanges'
import type { BaseTextRange } from '../../../lib/textRanges'
import { DecoratedText } from './DecoratedText'
import type { CollapsiblePath } from './types'

const SYNTAX_CLASSES: Record<SyntaxKind, string> = {
  plain: '', key: 'text-[var(--syntax-key)]', string: 'text-[var(--syntax-string)]',
  number: 'text-[var(--syntax-number)]', boolean: 'text-[var(--syntax-boolean)]',
  keyword: 'font-medium text-[var(--syntax-key)]', comment: 'italic text-[var(--text-subtle)]',
}

export function structuredCollapsiblePaths(nodes: StructuredNode[], depth = 0): CollapsiblePath[] {
  return nodes.flatMap((node) => node.children.length > 0
    ? [{ path: `line:${node.id}`, depth }, ...structuredCollapsiblePaths(node.children, depth + 1)]
    : [])
}

export function countStructuredMatches(nodes: StructuredNode[], query: string): number {
  return nodes.reduce((total, node) => total + findTextMatches(node.text.trimStart(), query).length + countStructuredMatches(node.children, query), 0)
}

function descendantLineCount(node: StructuredNode): number {
  return node.children.reduce((total, child) => total + 1 + descendantLineCount(child), 0)
}

function SyntaxLine({ text, language, query }: { text: string; language: ContentLanguage; query: string }) {
  const baseRanges: BaseTextRange[] = syntaxSegments(text, language).map((segment) => ({ start: segment.start, end: segment.end, className: SYNTAX_CLASSES[segment.kind] }))
  return <DecoratedText text={text} baseRanges={baseRanges} matches={findTextMatches(text, query)} structuredSearch />
}

function StructuredNodeView({ node, language, query, collapsedPaths, onToggle }: {
  node: StructuredNode
  language: ContentLanguage
  query: string
  collapsedPaths: Set<string>
  onToggle: (path: string) => void
}) {
  const path = `line:${node.id}`
  const foldable = node.children.length > 0
  const shownCollapsed = foldable && collapsedPaths.has(path) && query.length === 0
  const text = node.text.trimStart()
  return (
    <div>
      <div className="flex min-w-0 items-start">
        {foldable ? <button type="button" aria-label={`${shownCollapsed ? 'Expand' : 'Collapse'} line ${node.id + 1}`} aria-expanded={!shownCollapsed} disabled={query.length > 0} title={query ? 'Clear search to collapse this scope' : `${shownCollapsed ? 'Expand' : 'Collapse'} scope`} onClick={() => onToggle(path)} className="mr-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)] disabled:cursor-default disabled:opacity-50">{shownCollapsed ? '▶' : '▼'}</button> : <span className="mr-1 h-5 w-5 shrink-0" aria-hidden="true" />}
        <code className="min-w-0 break-words">{text ? <SyntaxLine text={text} language={language} query={query} /> : '\u00a0'}{shownCollapsed && <button type="button" onClick={() => onToggle(path)} className="ml-2 rounded px-1 text-[var(--text-subtle)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]">{descendantLineCount(node)} lines …</button>}</code>
      </div>
      {foldable && !shownCollapsed && <div className="ml-2.5 border-l border-[var(--border)] pl-3">{node.children.map((child) => <StructuredNodeView key={child.id} node={child} language={language} query={query} collapsedPaths={collapsedPaths} onToggle={onToggle} />)}</div>}
    </div>
  )
}

export function StructuredTextView({ nodes, language, languageLabel, query, collapsedPaths, onToggle }: {
  nodes: StructuredNode[]
  language: ContentLanguage
  languageLabel: string
  query: string
  collapsedPaths: Set<string>
  onToggle: (path: string) => void
}) {
  return <div role="region" aria-label={`Structured ${languageLabel}`}>{nodes.map((node) => <StructuredNodeView key={node.id} node={node} language={language} query={query} collapsedPaths={collapsedPaths} onToggle={onToggle} />)}</div>
}
