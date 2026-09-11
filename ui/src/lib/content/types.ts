interface DetectionResult {
  confidence: number
  reason: string
}

export type ContentLanguage = 'json' | 'xml' | 'toml' | 'yaml' | 'javascript' | 'python' | 'text'
type FormatStatus = 'formatted' | 'unchanged' | 'unsupported' | 'invalid'
export type SyntaxKind = 'plain' | 'key' | 'string' | 'number' | 'boolean' | 'keyword' | 'comment'

interface FormatResult {
  content: string
  status: FormatStatus
  preservesMeaning: boolean
}

export interface SyntaxSegment {
  start: number
  end: number
  kind: SyntaxKind
}

export interface StructuredNode {
  id: number
  text: string
  children: StructuredNode[]
}

export interface ContentLanguageAdapter {
  id: ContentLanguage
  label: string
  detect(content: string): DetectionResult
  format?(content: string): FormatResult
  highlight(line: string): SyntaxSegment[]
  structure?(content: string): StructuredNode[]
}

export interface AnalyzedContent {
  formatted: string
  canFormat: boolean
  formatStatus: FormatStatus
  language: ContentLanguage
  languageLabel: string
  adapter: ContentLanguageAdapter
}
