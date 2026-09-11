import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { tokenizeApi } from '../../api/client'
import { buildStructuredLines, findTextMatches, formattedContent, syntaxSegments } from '../../lib/searchableContent'
import { SearchableContentViewer } from './SearchableContentViewer'

afterEach(() => vi.restoreAllMocks())

describe('SearchableContentViewer', () => {
  it('finds every case-insensitive occurrence and cycles through them', async () => {
    render(<SearchableContentViewer title="Block content" content="Alpha beta alpha" />)
    await userEvent.type(screen.getByRole('searchbox', { name: /Search block content/i }), 'alpha')
    expect(screen.getByText('1 / 2')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Next occurrence' }))
    expect(screen.getByText('2 / 2')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Previous occurrence' }))
    expect(screen.getByText('1 / 2')).toBeTruthy()
  })

  it('offers four formatting modes and preserves verbatim JSON', async () => {
    render(<SearchableContentViewer title="Raw request payload" content={'{"name":"search"}'} />)
    expect(screen.getByRole('region', { name: 'Structured JSON' })).toBeTruthy()
    expect(screen.getByText('"name"').className).toContain('syntax-key')
    expect(screen.getByText('"search"').className).toContain('syntax-string')
    const formatter = screen.getByRole('combobox', { name: 'Formatting' }) as HTMLSelectElement
    expect(formatter.value).toBe('structured')
    expect(Array.from(formatter.options, (option) => option.text)).toEqual([
      'Verbatim raw', 'Formatted raw', 'Highlight tokens', 'Structured view',
    ])
    await userEvent.selectOptions(formatter, 'verbatim')
    expect(screen.getByText('{"name":"search"}')).toBeTruthy()
    await userEvent.selectOptions(formatter, 'formatted')
    expect(screen.getByText(/"name": "search"/)).toBeTruthy()
  })

  it('collapses and expands nested JSON scopes', async () => {
    render(<SearchableContentViewer title="Raw request payload" content={'{"meta":{"nested":{"enabled":true}}}'} />)
    expect(screen.getByText('true').className).toContain('syntax-boolean')
    expect(screen.getByRole('button', { name: 'Collapse' })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Collapse' }))
    expect(screen.queryByText('true')).toBeNull()
    expect(screen.getByText('"meta"')).toBeTruthy()
    expect(screen.getByText('"nested"')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Expand' })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Expand' }))
    expect(screen.getByText('true')).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Collapse meta' }))
    expect(screen.queryByText('true')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Expand meta' }))
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('keeps all modes available for plain text', () => {
    render(<SearchableContentViewer title="Block content" content="plain text" />)
    const formatter = screen.getByRole('combobox', { name: 'Formatting' }) as HTMLSelectElement
    expect(formatter.disabled).toBe(false)
    expect(screen.getByTitle('Detected content type: Text')).toBeTruthy()
  })

  it('structures and highlights XML with collapsible scopes', async () => {
    render(<SearchableContentViewer title="Block content" content={'<root><item enabled="true">value</item></root>'} />)
    expect(screen.getByRole('region', { name: 'Structured XML' })).toBeTruthy()
    expect(screen.getByText('<root>').className).toContain('syntax-key')
    expect(screen.getByText('<item enabled="true">').className).toContain('syntax-key')
    await userEvent.click(screen.getByRole('button', { name: 'Collapse line 1' }))
    expect(screen.queryByText('<item enabled="true">')).toBeNull()
  })

  it('restores per-token colors on formatted content', async () => {
    vi.spyOn(tokenizeApi, 'tokenize').mockResolvedValue({ results: [['Al', 'pha beta al', 'pha']] })
    const { container } = render(<SearchableContentViewer title="Block content" content="Alpha beta alpha" />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Formatting' }), 'tokens')
    await waitFor(() => expect(container.querySelectorAll('[style*="token-highlight"]')).toHaveLength(3))
    expect(tokenizeApi.tokenize).toHaveBeenCalledWith(['Alpha beta alpha'])
    await userEvent.type(screen.getByRole('searchbox', { name: /Search block content/i }), 'alpha')
    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(container.querySelectorAll('mark')).toHaveLength(2)
  })

  it('moves a capped token window to the content currently in view', async () => {
    const content = 'A'.repeat(100_000)
    vi.spyOn(tokenizeApi, 'tokenize').mockResolvedValue({ results: [['A'.repeat(100)]] })
    const { container } = render(<SearchableContentViewer title="Block content" content={content} />)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Formatting' }), 'tokens')
    const moveWindow = await screen.findByRole('button', { name: /First 1 token highlighted.*Move to current view/i })
    const viewport = container.querySelector('[data-content-viewport]') as HTMLDivElement
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 1_000 },
      scrollTop: { configurable: true, value: 800, writable: true },
    })

    await userEvent.click(moveWindow)

    await waitFor(() => expect(tokenizeApi.tokenize).toHaveBeenCalledTimes(2))
    expect(tokenizeApi.tokenize).toHaveBeenLastCalledWith([content.slice(60_000)])
    expect(await screen.findByRole('button', { name: /1 token highlighted.*Move to current view/i })).toBeTruthy()
  })
})

describe('content helpers', () => {
  it('detects and formats common structured content', () => {
    expect(formattedContent('{"ok":true}')).toEqual({
      formatted: '{\n  "ok": true\n}', canFormat: true, language: 'json', languageLabel: 'JSON',
    })
    expect(formattedContent('<root><item>value</item></root>')).toMatchObject({
      formatted: '<root>\n  <item>\n    value\n  </item>\n</root>', language: 'xml', languageLabel: 'XML',
    })
    expect(formattedContent('[server]\nport=8080')).toMatchObject({
      formatted: '[server]\nport = 8080', language: 'toml', languageLabel: 'TOML',
    })
    expect(formattedContent('service:\n  enabled: true')).toMatchObject({ language: 'yaml', languageLabel: 'YAML' })
    expect(formattedContent('const value={ok:true};')).toMatchObject({ language: 'javascript', languageLabel: 'JavaScript' })
    expect(formattedContent('def run():\n\treturn True')).toMatchObject({
      formatted: 'def run():\n    return True', language: 'python', languageLabel: 'Python',
    })
    expect(formattedContent('plain text')).toEqual({
      formatted: 'plain text', canFormat: false, language: 'text', languageLabel: 'Text',
    })
  })

  it('returns non-overlapping text matches', () => {
    expect(findTextMatches('One one ONE', 'one')).toEqual([{ start: 0, end: 3 }, { start: 4, end: 7 }, { start: 8, end: 11 }])
  })

  it('builds indentation scopes and classifies code syntax', () => {
    const python = buildStructuredLines('def run():\n    if ready:\n        return True\n    return False', 'python')
    expect(python).toHaveLength(1)
    expect(python[0].children).toHaveLength(2)
    expect(python[0].children[0].children[0].text.trim()).toBe('return True')

    const kinds = syntaxSegments('const ready = true // state', 'javascript').map((segment) => segment.kind)
    expect(kinds).toEqual(expect.arrayContaining(['keyword', 'boolean', 'comment']))
  })
})
