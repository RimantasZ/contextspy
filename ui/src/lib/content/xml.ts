import { highlightLine } from './highlighter'
import { buildIndentationTree } from './structure'
import type { ContentLanguageAdapter } from './types'

function parseXml(content: string): Document | null {
  const trimmed = content.trim()
  if (!/^<\??[A-Za-z_!][\s\S]*>$/.test(trimmed)) return null
  if (typeof DOMParser === 'undefined') return /^<[^>]+>[\s\S]*<\/[^>]+>$/.test(trimmed) ? ({} as Document) : null
  const document = new DOMParser().parseFromString(trimmed, 'application/xml')
  return document.getElementsByTagName('parsererror').length === 0 ? document : null
}

function hasSignificantText(document: Document | null, content: string): boolean {
  if (!document?.documentElement) return />[^<\s][^<]*</.test(content)
  const walker = document.createTreeWalker(document, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    if (node.textContent?.trim()) return true
    node = walker.nextNode()
  }
  return false
}

function layoutXml(content: string): string {
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

export const xmlAdapter: ContentLanguageAdapter = {
  id: 'xml',
  label: 'XML',
  detect(content) {
    return parseXml(content)
      ? { confidence: 0.95, reason: 'valid XML document' }
      : { confidence: 0, reason: 'not valid XML' }
  },
  format(content) {
    const document = parseXml(content)
    if (!document) return { content, status: 'invalid', preservesMeaning: true }
    if (/xml:space\s*=\s*["']preserve["']/.test(content) || hasSignificantText(document, content)) {
      return { content, status: 'unsupported', preservesMeaning: true }
    }
    return { content: layoutXml(content), status: 'formatted', preservesMeaning: true }
  },
  highlight(line) { return highlightLine(line, 'xml') },
  structure(content) { return buildIndentationTree(layoutXml(content)) },
}
