import { highlightLine } from './highlighter'
import { buildIndentationTree } from './structure'
import type { ContentLanguageAdapter } from './types'

export const textAdapter: ContentLanguageAdapter = {
  id: 'text',
  label: 'Text',
  detect() { return { confidence: 0.01, reason: 'plain-text fallback' } },
  format(content) { return { content, status: 'unsupported', preservesMeaning: true } },
  highlight(line) { return highlightLine(line, 'text') },
  structure(content) { return buildIndentationTree(content) },
}
