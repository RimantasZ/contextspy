import type { ContentLanguage, SyntaxKind, SyntaxSegment } from './types'

const CODE_KEYWORDS = new Set([
  'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'def', 'default', 'delete', 'do',
  'elif', 'else', 'except', 'export', 'extends', 'finally', 'for', 'from', 'function', 'if', 'implements',
  'import', 'in', 'instanceof', 'interface', 'lambda', 'let', 'new', 'of', 'pass', 'raise', 'return', 'static',
  'super', 'switch', 'throw', 'try', 'type', 'typeof', 'var', 'while', 'with', 'yield',
])

const BOOLEAN_LITERALS = new Set(['false', 'none', 'null', 'true', 'undefined'])

function pushSegment(segments: SyntaxSegment[], start: number, end: number, kind: SyntaxKind) {
  if (end > start) segments.push({ start, end, kind })
}

function xmlSegments(line: string): SyntaxSegment[] {
  const segments: SyntaxSegment[] = []
  const expression = /<!--[\s\S]*?-->|<[^>]+>/g
  let offset = 0
  for (const match of line.matchAll(expression)) {
    const start = match.index ?? 0
    pushSegment(segments, offset, start, 'plain')
    pushSegment(segments, start, start + match[0].length, match[0].startsWith('<!--') ? 'comment' : 'key')
    offset = start + match[0].length
  }
  pushSegment(segments, offset, line.length, 'plain')
  return segments
}

export function highlightLine(line: string, language: ContentLanguage): SyntaxSegment[] {
  if (!line) return []
  if (language === 'xml') return xmlSegments(line)

  const segments: SyntaxSegment[] = []
  const expression = /\/\/.*$|#.*$|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b(?:0x[\da-f]+|\d+(?:\.\d+)?)\b|\b[A-Za-z_$][\w$]*\b/gi
  let offset = 0
  for (const match of line.matchAll(expression)) {
    const start = match.index ?? 0
    const token = match[0]
    pushSegment(segments, offset, start, 'plain')
    let kind: SyntaxKind = 'plain'
    if (token.startsWith('//') || token.startsWith('#') || token.startsWith('/*')) kind = 'comment'
    else if (/^["'`]/.test(token)) {
      const after = line.slice(start + token.length)
      kind = language === 'json' && /^\s*:/.test(after) ? 'key' : 'string'
    } else if (/^(?:0x[\da-f]+|\d)/i.test(token)) kind = 'number'
    else if (BOOLEAN_LITERALS.has(token.toLocaleLowerCase())) kind = 'boolean'
    else if (CODE_KEYWORDS.has(token.toLocaleLowerCase())) kind = 'keyword'
    pushSegment(segments, start, start + token.length, kind)
    offset = start + token.length
  }
  pushSegment(segments, offset, line.length, 'plain')

  if (language === 'toml' || language === 'yaml') {
    const section = language === 'toml' && /^\s*\[\[?.+\]?\]\s*$/.test(line)
    if (section) return [{ start: 0, end: line.length, kind: 'key' }]
    const key = line.match(language === 'toml'
      ? /^(\s*[A-Za-z0-9_.-]+)(?=\s*=)/
      : /^(\s*(?:-\s*)?[A-Za-z0-9_.-]+)(?=\s*:)/)
    if (key) {
      return [
        { start: 0, end: key[0].length, kind: 'key' },
        ...segments
          .filter((segment) => segment.end > key[0].length)
          .map((segment) => ({ ...segment, start: Math.max(segment.start, key[0].length) })),
      ]
    }
  }
  return segments
}
