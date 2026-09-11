import { highlightLine } from './highlighter'
import { buildIndentationTree } from './structure'
import type { ContentLanguageAdapter } from './types'

export const yamlAdapter: ContentLanguageAdapter = {
  id: 'yaml',
  label: 'YAML',
  detect(content) {
    const detected = /^(?:---\s*$|\s*[A-Za-z0-9_.-]+:\s*(?:.+)?$)/m.test(content)
    return detected
      ? { confidence: 0.62, reason: 'YAML key or document marker' }
      : { confidence: 0, reason: 'no YAML syntax' }
  },
  format(content) { return { content, status: 'unsupported', preservesMeaning: true } },
  highlight(line) { return highlightLine(line, 'yaml') },
  structure(content) { return buildIndentationTree(content) },
}
