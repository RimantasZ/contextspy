import { highlightLine } from './highlighter'
import { buildIndentationTree } from './structure'
import type { ContentLanguageAdapter } from './types'

export const javascriptAdapter: ContentLanguageAdapter = {
  id: 'javascript',
  label: 'JavaScript',
  detect(content) {
    const strong = /\b(?:const|let|var|function|export|interface)\b/.test(content) || /=>/.test(content)
      || /(?:===|!==|\b(?:console|document|window)\.)/.test(content)
    const braces = /[{}]\s*;?\s*$/.test(content.trim())
    return strong || braces
      ? { confidence: strong ? 0.82 : 0.56, reason: strong ? 'JavaScript keyword or operator' : 'brace-delimited code' }
      : { confidence: 0, reason: 'no JavaScript syntax' }
  },
  format(content) {
    return { content, status: 'unsupported', preservesMeaning: true }
  },
  highlight(line) { return highlightLine(line, 'javascript') },
  structure(content) { return buildIndentationTree(content) },
}
