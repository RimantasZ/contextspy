import { describe, expect, it } from 'vitest'
import { CONTENT_FIXTURES } from '../../test/contentFixtures'
import { analyzeContent, buildStructuredLines, syntaxSegments } from '.'

describe('content language registry', () => {
  it.each([
    ['json', CONTENT_FIXTURES.json, 'json'],
    ['xml', CONTENT_FIXTURES.xml, 'xml'],
    ['toml', CONTENT_FIXTURES.toml, 'toml'],
    ['javascript', CONTENT_FIXTURES.javascript, 'javascript'],
    ['python', CONTENT_FIXTURES.python, 'python'],
    ['yaml', CONTENT_FIXTURES.yaml, 'yaml'],
  ] as const)('detects %s content', (_name, content, language) => {
    expect(analyzeContent(content).language).toBe(language)
  })

  it('formats JSON and simple TOML deterministically', () => {
    for (const content of [CONTENT_FIXTURES.json, CONTENT_FIXTURES.toml]) {
      const once = analyzeContent(content)
      const twice = analyzeContent(once.formatted)
      expect(twice.formatted).toBe(once.formatted)
      expect(once.formatStatus).toBe('formatted')
    }
  })

  it('preserves mixed XML and code when safe formatting is unavailable', () => {
    for (const content of [CONTENT_FIXTURES.mixedXml, CONTENT_FIXTURES.javascript, CONTENT_FIXTURES.python]) {
      const analysis = analyzeContent(content)
      expect(analysis.formatted).toBe(content)
      expect(analysis.formatStatus).toBe('unsupported')
    }
  })

  it('provides safe plain-text fallback for empty and invalid content', () => {
    expect(analyzeContent(CONTENT_FIXTURES.empty)).toMatchObject({ language: 'text', formatted: '', formatStatus: 'unsupported' })
    expect(analyzeContent('{invalid')).toMatchObject({ formatted: '{invalid' })
  })

  it('builds structure and syntax through the selected adapter', () => {
    const tree = buildStructuredLines(CONTENT_FIXTURES.python, 'python')
    expect(tree[0].children.length).toBeGreaterThan(0)
    expect(syntaxSegments('const ready = true // state', 'javascript').map((segment) => segment.kind))
      .toEqual(expect.arrayContaining(['keyword', 'boolean', 'comment']))
  })
})
