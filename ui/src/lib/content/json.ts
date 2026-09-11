import { highlightLine } from './highlighter'
import type { ContentLanguageAdapter } from './types'

export const jsonAdapter: ContentLanguageAdapter = {
  id: 'json',
  label: 'JSON',
  detect(content) {
    const trimmed = content.trim()
    if (!trimmed) return { confidence: 0, reason: 'empty' }
    try {
      JSON.parse(trimmed)
      return { confidence: 1, reason: 'valid JSON' }
    } catch {
      return { confidence: 0, reason: 'invalid JSON' }
    }
  },
  format(content) {
    try {
      return { content: JSON.stringify(JSON.parse(content), null, 2), status: 'formatted', preservesMeaning: true }
    } catch {
      return { content, status: 'invalid', preservesMeaning: true }
    }
  },
  highlight(line) { return highlightLine(line, 'json') },
}
