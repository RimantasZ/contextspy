import { findTextMatches } from '../../../lib/textRanges'
import { DecoratedText } from './DecoratedText'
import type { CollapsiblePath, JsonValue } from './types'

function pathPart(value: string | number): string { return String(value).replace(/~/g, '~0').replace(/\//g, '~1') }

export function jsonCollapsiblePaths(value: JsonValue, path = '$', depth = 0): CollapsiblePath[] {
  if (value === null || typeof value !== 'object') return []
  const entries = Array.isArray(value) ? value.map((child, index) => [index, child] as const) : Object.entries(value)
  if (entries.length === 0) return []
  return [{ path, depth }, ...entries.flatMap(([key, child]) => jsonCollapsiblePaths(child, `${path}/${pathPart(key)}`, depth + 1))]
}

export function countJsonMatches(value: JsonValue, query: string): number {
  if (!query) return 0
  if (Array.isArray(value)) return value.reduce<number>((total, child) => total + countJsonMatches(child, query), 0)
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).reduce<number>((total, [key, child]) => total + findTextMatches(JSON.stringify(key), query).length + countJsonMatches(child, query), 0)
  }
  return findTextMatches(JSON.stringify(value), query).length
}

function JsonText({ text, query }: { text: string; query: string }) {
  return <DecoratedText text={text} matches={findTextMatches(text, query)} structuredSearch />
}
function JsonScalar({ value, query }: { value: string | number | boolean | null; query: string }) {
  if (value === null) return <span className="text-[var(--text-muted)]"><JsonText text="null" query={query} /></span>
  if (typeof value === 'boolean') return <span className="text-[var(--syntax-boolean)]"><JsonText text={String(value)} query={query} /></span>
  if (typeof value === 'number') return <span className="text-[var(--syntax-number)]"><JsonText text={String(value)} query={query} /></span>
  return <span className="text-[var(--syntax-string)]"><JsonText text={JSON.stringify(value)} query={query} /></span>
}
function JsonKey({ name, query }: { name: string; query: string }) {
  return <><span className="text-[var(--syntax-key)]"><JsonText text={JSON.stringify(name)} query={query} /></span><span className="text-[var(--text-muted)]">: </span></>
}

function JsonTreeNode({ value, query, collapsedPaths, onToggle, path = '$', name, trailingComma = false }: {
  value: JsonValue
  query: string
  collapsedPaths: Set<string>
  onToggle: (path: string) => void
  path?: string
  name?: string
  trailingComma?: boolean
}) {
  if (value === null || typeof value !== 'object') {
    return <div className="min-w-0 break-words">{name != null && <JsonKey name={name} query={query} />}<JsonScalar value={value as string | number | boolean | null} query={query} />{trailingComma && <span className="text-[var(--text-subtle)]">,</span>}</div>
  }
  const array = Array.isArray(value)
  const entries: Array<[string | number, JsonValue]> = array ? value.map((child, index) => [index, child]) : Object.entries(value)
  const open = array ? '[' : '{'
  const close = array ? ']' : '}'
  if (entries.length === 0) return <div>{name != null && <JsonKey name={name} query={query} />}<span className="text-[var(--text-muted)]">{open}{close}</span>{trailingComma && <span className="text-[var(--text-subtle)]">,</span>}</div>

  const shownCollapsed = collapsedPaths.has(path) && query.length === 0
  const scopeName = name ?? (array ? 'array' : 'object')
  return (
    <div>
      <div className="flex min-w-0 items-start">
        <button type="button" aria-label={`${shownCollapsed ? 'Expand' : 'Collapse'} ${scopeName}`} aria-expanded={!shownCollapsed} disabled={query.length > 0} title={query ? 'Clear search to collapse this scope' : `${shownCollapsed ? 'Expand' : 'Collapse'} ${scopeName}`} onClick={() => onToggle(path)} className="mr-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)] disabled:cursor-default disabled:opacity-50">{shownCollapsed ? '▶' : '▼'}</button>
        <div className="min-w-0 break-words">
          {name != null && <JsonKey name={name} query={query} />}<span className="text-[var(--text-muted)]">{open}</span>
          {shownCollapsed && <><button type="button" onClick={() => onToggle(path)} className="mx-1 rounded px-1 text-[var(--text-subtle)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]">{entries.length} {array ? `item${entries.length === 1 ? '' : 's'}` : `key${entries.length === 1 ? '' : 's'}`} …</button><span className="text-[var(--text-muted)]">{close}</span>{trailingComma && <span className="text-[var(--text-subtle)]">,</span>}</>}
        </div>
      </div>
      {!shownCollapsed && <><div className="ml-2.5 border-l border-[var(--border)] pl-3">{entries.map(([key, child], index) => <JsonTreeNode key={key} value={child} name={array ? undefined : String(key)} path={`${path}/${pathPart(key)}`} query={query} trailingComma={index < entries.length - 1} collapsedPaths={collapsedPaths} onToggle={onToggle} />)}</div><div className="pl-5 text-[var(--text-muted)]">{close}{trailingComma && <span className="text-[var(--text-subtle)]">,</span>}</div></>}
    </div>
  )
}

export function JsonTreeView({ value, query, collapsedPaths, onToggle }: { value: JsonValue; query: string; collapsedPaths: Set<string>; onToggle: (path: string) => void }) {
  return <div role="region" aria-label="Structured JSON"><JsonTreeNode value={value} query={query} collapsedPaths={collapsedPaths} onToggle={onToggle} /></div>
}
