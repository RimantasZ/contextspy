export interface TextMatch {
  start: number
  end: number
}

export type ContentLanguage = 'json' | 'xml' | 'toml' | 'yaml' | 'javascript' | 'python' | 'text'

export interface FormattedContent {
  formatted: string
  canFormat: boolean
  language: ContentLanguage
  languageLabel: string
}

export type SyntaxKind = 'plain' | 'key' | 'string' | 'number' | 'boolean' | 'keyword' | 'comment'

export interface SyntaxSegment {
  start: number
  end: number
  kind: SyntaxKind
}

export interface StructuredLine {
  id: number
  text: string
  children: StructuredLine[]
}

const LANGUAGE_LABELS: Record<ContentLanguage, string> = {
  json: 'JSON',
  xml: 'XML',
  toml: 'TOML',
  yaml: 'YAML',
  javascript: 'JavaScript',
  python: 'Python',
  text: 'Text',
}

const CODE_KEYWORDS = new Set([
  'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'def', 'default', 'delete', 'do',
  'elif', 'else', 'except', 'export', 'extends', 'finally', 'for', 'from', 'function', 'if', 'implements',
  'import', 'in', 'instanceof', 'interface', 'lambda', 'let', 'new', 'of', 'pass', 'raise', 'return', 'static',
  'super', 'switch', 'throw', 'try', 'type', 'typeof', 'var', 'while', 'with', 'yield',
])

const BOOLEAN_LITERALS = new Set(['false', 'none', 'null', 'true', 'undefined'])

function normalizeLines(content: string): string {
  return content.replace(/\r\n?/g, '\n').split('\n').map((line) => line.replace(/[ \t]+$/g, '')).join('\n')
}

function looksLikeXml(content: string): boolean {
  const trimmed = content.trim()
  if (!/^<\??[A-Za-z_!][\s\S]*>$/.test(trimmed)) return false
  if (typeof DOMParser === 'undefined') return /^<[^>]+>[\s\S]*<\/[^>]+>$/.test(trimmed)
  const document = new DOMParser().parseFromString(trimmed, 'application/xml')
  return document.getElementsByTagName('parsererror').length === 0
}

function detectLanguage(content: string): ContentLanguage {
  const trimmed = content.trim()
  if (!trimmed) return 'text'
  try {
    JSON.parse(trimmed)
    return 'json'
  } catch {
    // Continue with the lighter language heuristics below.
  }
  if (looksLikeXml(trimmed)) return 'xml'
  if (/^(?:#!.*python|\s*(?:async\s+def|def|class|from\s+\S+\s+import|import\s+\S+)\b)/m.test(trimmed)
    || /^\s*(?:if|elif|for|while|try|except|with)\b[^\n]*:\s*$/m.test(trimmed)
    || /\b(?:True|False|None|self|lambda)\b/.test(trimmed)) return 'python'
  if (/\b(?:const|let|var|function|export|interface)\b/.test(trimmed) || /=>/.test(trimmed)
    || /(?:===|!==|\b(?:console|document|window)\.)/.test(trimmed) || /[{}]\s*;?\s*$/.test(trimmed)) return 'javascript'
  if (/^\s*\[\[?[A-Za-z0-9_.-]+\]?\]\s*$/m.test(trimmed)
    || /^\s*[A-Za-z0-9_.-]+\s*=\s*.+$/m.test(trimmed)) return 'toml'
  if (/^(?:---\s*$|\s*[A-Za-z0-9_.-]+:\s*(?:.+)?$)/m.test(trimmed)) return 'yaml'
  return 'text'
}

function formatXml(content: string): string {
  const tokens = content.trim().match(/<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<![^>]*>|<[^>]+>|[^<]+/g) ?? []
  const lines: string[] = []
  let indent = 0
  tokens.forEach((rawToken) => {
    const token = rawToken.trim()
    if (!token) return
    const closing = /^<\//.test(token)
    if (closing) indent = Math.max(0, indent - 1)
    lines.push(`${'  '.repeat(indent)}${token}`)
    const opening = /^<[A-Za-z_][^>]*>$/.test(token) && !/\/>$/.test(token) && !/<\/[^>]+>$/.test(token)
    if (opening) indent += 1
  })
  return lines.join('\n')
}

function formatToml(content: string): string {
  const lines = normalizeLines(content).split('\n')
  const formatted: string[] = []
  lines.forEach((line) => {
    const section = /^\s*\[\[?.+\]?\]\s*$/.test(line)
    if (section && formatted.length > 0 && formatted[formatted.length - 1] !== '') formatted.push('')
    const assignment = line.match(/^(\s*[A-Za-z0-9_.-]+)\s*=\s*(.*)$/)
    formatted.push(assignment ? `${assignment[1]} = ${assignment[2]}` : line)
  })
  return formatted.join('\n')
}

function formatBraceCode(content: string): string {
  const normalized = normalizeLines(content)
  const existingLines = normalized.split('\n')
  const longestLine = existingLines.reduce((longest, line) => Math.max(longest, line.length), 0)
  if (existingLines.length > 2 && longestLine < 180) return normalized

  const lines: string[] = []
  let line = ''
  let indent = 0
  let quote = ''
  let escaped = false
  let lineComment = false
  let blockComment = false

  const flush = () => {
    const text = line.trim()
    if (text) lines.push(`${'  '.repeat(indent)}${text}`)
    line = ''
  }

  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index]
    const next = normalized[index + 1] ?? ''
    if (lineComment) {
      if (char === '\n') { flush(); lineComment = false } else line += char
      continue
    }
    if (blockComment) {
      line += char
      if (char === '*' && next === '/') { line += next; index += 1; blockComment = false }
      if (char === '\n') flush()
      continue
    }
    if (quote) {
      line += char
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === quote) quote = ''
      continue
    }
    if (char === '/' && next === '/') { line += '//'; index += 1; lineComment = true; continue }
    if (char === '/' && next === '*') { line += '/*'; index += 1; blockComment = true; continue }
    if (char === '"' || char === "'" || char === '`') { quote = char; line += char; continue }
    if (char === '{') { line += char; flush(); indent += 1; continue }
    if (char === '}') {
      flush()
      indent = Math.max(0, indent - 1)
      line = '}'
      if (!/[;,)]/.test(next)) flush()
      continue
    }
    if (char === ';') { line += char; flush(); continue }
    if (char === '\n') { flush(); continue }
    if (/\s/.test(char)) {
      if (line && !/\s$/.test(line)) line += ' '
      continue
    }
    line += char
  }
  flush()
  return lines.join('\n')
}

export function formattedContent(content: string): FormattedContent {
  const language = detectLanguage(content)
  let formatted = normalizeLines(content)
  if (language === 'json') formatted = JSON.stringify(JSON.parse(content), null, 2)
  else if (language === 'xml') formatted = formatXml(content)
  else if (language === 'toml') formatted = formatToml(content)
  else if (language === 'javascript') formatted = formatBraceCode(content)
  else if (language === 'python') formatted = normalizeLines(content).replace(/^\t+/gm, (tabs) => '    '.repeat(tabs.length))
  return { formatted, canFormat: language !== 'text', language, languageLabel: LANGUAGE_LABELS[language] }
}

function pushSegment(segments: SyntaxSegment[], start: number, end: number, kind: SyntaxKind) {
  if (end > start) segments.push({ start, end, kind })
}

export function syntaxSegments(line: string, language: ContentLanguage): SyntaxSegment[] {
  if (!line) return []
  if (language === 'xml') {
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
        ...segments.filter((segment) => segment.end > key[0].length).map((segment) => ({ ...segment, start: Math.max(segment.start, key[0].length) })),
      ]
    }
  }
  return segments
}

function indentation(line: string): number {
  const leading = line.match(/^[ \t]*/)?.[0] ?? ''
  return [...leading].reduce((total, char) => total + (char === '\t' ? 4 : 1), 0)
}

export function buildStructuredLines(content: string, language: ContentLanguage): StructuredLine[] {
  const lines = content.split('\n')
  let nextId = 0
  if (language === 'toml') {
    const roots: StructuredLine[] = []
    let section: StructuredLine | null = null
    lines.forEach((text) => {
      const node = { id: nextId++, text, children: [] }
      if (/^\s*\[\[?.+\]?\]\s*$/.test(text)) {
        roots.push(node)
        section = node
      } else if (section) section.children.push(node)
      else roots.push(node)
    })
    return roots
  }

  const roots: StructuredLine[] = []
  const stack: Array<{ indent: number; node: StructuredLine }> = []
  lines.forEach((text) => {
    const node = { id: nextId++, text, children: [] }
    if (!text.trim()) {
      if (stack.length > 0) stack[stack.length - 1].node.children.push(node)
      else roots.push(node)
      return
    }
    const level = indentation(text)
    while (stack.length > 0 && level <= stack[stack.length - 1].indent) stack.pop()
    if (stack.length > 0) stack[stack.length - 1].node.children.push(node)
    else roots.push(node)
    stack.push({ indent: level, node })
  })
  return roots
}

export function findTextMatches(text: string, query: string): TextMatch[] {
  const needle = query.toLocaleLowerCase()
  if (!needle) return []
  const haystack = text.toLocaleLowerCase()
  const matches: TextMatch[] = []
  let offset = 0
  while (offset <= haystack.length - needle.length) {
    const start = haystack.indexOf(needle, offset)
    if (start < 0) break
    matches.push({ start, end: start + needle.length })
    offset = start + Math.max(1, needle.length)
  }
  return matches
}
