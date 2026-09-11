import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { tokenizeApi } from '../../api/client'
import { analyzeContent, buildStructuredLines, syntaxSegments } from '../../lib/content'
import { findTextMatches } from '../../lib/textRanges'
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
    expect(screen.getByRole('button', { name: 'Expand' })).toBeTruthy()
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
    vi.spyOn(tokenizeApi, 'window').mockResolvedValue({
      segments: [
        { text: 'Al', start: 0, end: 2, token_count: 1 },
        { text: 'pha beta al', start: 2, end: 13, token_count: 1 },
        { text: 'pha', start: 13, end: 16, token_count: 1 },
      ],
      window_start: 0, window_end: 16, total_length: 16,
      truncated_before: false, truncated_after: false, tokenizer: 'tiktoken/o200k_base',
    })
    const { container } = render(<SearchableContentViewer title="Block content" content="Alpha beta alpha" />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Formatting' }), 'tokens')
    await waitFor(() => expect(container.querySelectorAll('[style*="token-highlight"]')).toHaveLength(3))
    expect(tokenizeApi.window).toHaveBeenCalledWith('Alpha beta alpha', 0, expect.any(AbortSignal))
    await userEvent.type(screen.getByRole('searchbox', { name: /Search block content/i }), 'alpha')
    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(container.querySelectorAll('mark')).toHaveLength(2)
  })

  it('moves a capped token window to the content currently in view', async () => {
    const content = 'A'.repeat(100_000)
    vi.spyOn(tokenizeApi, 'window').mockImplementation(async (_text, offset) => ({
      segments: [{ text: content.slice(offset, offset + 50_000), start: offset, end: Math.min(offset + 50_000, content.length), token_count: 8_000 }],
      window_start: offset, window_end: Math.min(offset + 50_000, content.length), total_length: content.length,
      truncated_before: offset > 0, truncated_after: offset + 50_000 < content.length, tokenizer: 'tiktoken/o200k_base',
    }))
    const { container } = render(<SearchableContentViewer title="Block content" content={content} />)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Formatting' }), 'tokens')
    const moveWindow = await screen.findByRole('button', { name: /First 8,000 tokens highlighted.*Move to current view/i })
    const viewport = container.querySelector('[data-content-viewport]') as HTMLDivElement
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 1_000 },
      scrollTop: { configurable: true, value: 800, writable: true },
    })

    await userEvent.click(moveWindow)

    await waitFor(() => expect(tokenizeApi.window).toHaveBeenCalledTimes(2))
    expect(tokenizeApi.window).toHaveBeenLastCalledWith(content, 60_000, expect.any(AbortSignal))
    expect(await screen.findByRole('button', { name: /8,000 tokens highlighted.*Move to current view/i })).toBeTruthy()
  })

  it('ignores a stale token response after content changes', async () => {
    let resolveFirst!: (value: Awaited<ReturnType<typeof tokenizeApi.window>>) => void
    let resolveSecond!: (value: Awaited<ReturnType<typeof tokenizeApi.window>>) => void
    vi.spyOn(tokenizeApi, 'window')
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
    const { container, rerender } = render(<SearchableContentViewer title="Block content" content="old content" />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Formatting' }), 'tokens')
    await waitFor(() => expect(tokenizeApi.window).toHaveBeenCalledTimes(1))

    rerender(<SearchableContentViewer title="Block content" content="new content" />)
    await waitFor(() => expect(tokenizeApi.window).toHaveBeenCalledTimes(2))
    resolveFirst({
      segments: [{ text: 'old content', start: 0, end: 11, token_count: 2 }],
      window_start: 0, window_end: 11, total_length: 11,
      truncated_before: false, truncated_after: false, tokenizer: 'test',
    })
    await Promise.resolve()
    expect(container.textContent).not.toContain('old content')

    resolveSecond({
      segments: [{ text: 'new content', start: 0, end: 11, token_count: 2 }],
      window_start: 0, window_end: 11, total_length: 11,
      truncated_before: false, truncated_after: false, tokenizer: 'test',
    })
    await waitFor(() => expect(container.querySelectorAll('[style*="token-highlight"]')).toHaveLength(1))
  })
})

describe('content helpers', () => {
  it('detects and formats common structured content', () => {
    expect(analyzeContent('{"ok":true}')).toMatchObject({
      formatted: '{\n  "ok": true\n}', canFormat: true, formatStatus: 'formatted', language: 'json', languageLabel: 'JSON',
    })
    expect(analyzeContent('<root><item>value</item></root>')).toMatchObject({
      formatted: '<root><item>value</item></root>', formatStatus: 'unsupported', language: 'xml', languageLabel: 'XML',
    })
    expect(analyzeContent('[server]\nport=8080')).toMatchObject({
      formatted: '[server]\nport = 8080', language: 'toml', languageLabel: 'TOML',
    })
    expect(analyzeContent('service:\n  enabled: true')).toMatchObject({ language: 'yaml', languageLabel: 'YAML' })
    expect(analyzeContent('const value={ok:true};')).toMatchObject({ language: 'javascript', languageLabel: 'JavaScript' })
    expect(analyzeContent('def run():\n\treturn True')).toMatchObject({
      formatted: 'def run():\n\treturn True', formatStatus: 'unsupported', language: 'python', languageLabel: 'Python',
    })
    expect(analyzeContent('plain text')).toMatchObject({
      formatted: 'plain text', canFormat: false, formatStatus: 'unsupported', language: 'text', languageLabel: 'Text',
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
