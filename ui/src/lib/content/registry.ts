import { javascriptAdapter } from './javascript'
import { jsonAdapter } from './json'
import { pythonAdapter } from './python'
import { textAdapter } from './text'
import { tomlAdapter } from './toml'
import type { AnalyzedContent, ContentLanguage, ContentLanguageAdapter, StructuredNode, SyntaxSegment } from './types'
import { xmlAdapter } from './xml'
import { yamlAdapter } from './yaml'

const DETECTABLE_ADAPTERS = [jsonAdapter, xmlAdapter, pythonAdapter, javascriptAdapter, tomlAdapter, yamlAdapter]
const ADAPTERS = new Map<ContentLanguage, ContentLanguageAdapter>(
  [...DETECTABLE_ADAPTERS, textAdapter].map((adapter) => [adapter.id, adapter]),
)

function adapterFor(language: ContentLanguage): ContentLanguageAdapter {
  return ADAPTERS.get(language) ?? textAdapter
}

export function analyzeContent(content: string): AnalyzedContent {
  const adapter = DETECTABLE_ADAPTERS
    .map((candidate) => ({ candidate, detection: candidate.detect(content) }))
    .sort((left, right) => right.detection.confidence - left.detection.confidence)[0]

  const selected = adapter && adapter.detection.confidence > 0 ? adapter.candidate : textAdapter
  const format = selected.format?.(content) ?? { content, status: 'unsupported' as const, preservesMeaning: true }
  return {
    formatted: format.content,
    canFormat: format.status === 'formatted',
    formatStatus: format.status,
    language: selected.id,
    languageLabel: selected.label,
    adapter: selected,
  }
}

export function syntaxSegments(line: string, language: ContentLanguage): SyntaxSegment[] {
  return adapterFor(language).highlight(line)
}

export function buildStructuredLines(content: string, language: ContentLanguage): StructuredNode[] {
  return adapterFor(language).structure?.(content) ?? []
}
