import { highlightLine } from './highlighter'
import { buildTomlTree } from './structure'
import type { ContentLanguageAdapter } from './types'

function formatToml(content: string): string {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const formatted: string[] = []
  lines.forEach((line) => {
    const section = /^\s*\[\[?.+\]?\]\s*$/.test(line)
    if (section && formatted.length > 0 && formatted[formatted.length - 1] !== '') formatted.push('')
    const assignment = line.match(/^(\s*[A-Za-z0-9_.-]+)\s*=\s*(.*)$/)
    formatted.push(assignment ? `${assignment[1]} = ${assignment[2]}` : line)
  })
  return formatted.join('\n')
}

export const tomlAdapter: ContentLanguageAdapter = {
  id: 'toml',
  label: 'TOML',
  detect(content) {
    const section = /^\s*\[\[?[A-Za-z0-9_.-]+\]?\]\s*$/m.test(content)
    const assignment = /^\s*[A-Za-z0-9_.-]+\s*=\s*.+$/m.test(content)
    return section || assignment
      ? { confidence: section ? 0.84 : 0.72, reason: section ? 'TOML section' : 'TOML assignment' }
      : { confidence: 0, reason: 'no TOML syntax' }
  },
  format(content) {
    if (/(?:'''|""")/.test(content)) return { content, status: 'unsupported', preservesMeaning: true }
    return { content: formatToml(content), status: 'formatted', preservesMeaning: true }
  },
  highlight(line) { return highlightLine(line, 'toml') },
  structure(content) { return buildTomlTree(content) },
}
