import { highlightLine } from './highlighter'
import { buildIndentationTree } from './structure'
import type { ContentLanguageAdapter } from './types'

export const pythonAdapter: ContentLanguageAdapter = {
  id: 'python',
  label: 'Python',
  detect(content) {
    const declaration = /^(?:#!.*python|\s*(?:async\s+def|def|class|from\s+\S+\s+import|import\s+\S+)\b)/m.test(content)
    const suite = /^\s*(?:if|elif|for|while|try|except|with)\b[^\n]*:\s*$/m.test(content)
    const literal = /\b(?:True|False|None|self|lambda)\b/.test(content)
    return declaration || suite || literal
      ? { confidence: declaration ? 0.88 : 0.74, reason: declaration ? 'Python declaration' : 'Python suite or literal' }
      : { confidence: 0, reason: 'no Python syntax' }
  },
  format(content) {
    return { content, status: 'unsupported', preservesMeaning: true }
  },
  highlight(line) { return highlightLine(line, 'python') },
  structure(content) { return buildIndentationTree(content) },
}
