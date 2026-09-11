import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { findTextMatches, formattedContent } from '../../lib/searchableContent'
import { SearchableContentViewer } from './SearchableContentViewer'

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

  it('formats JSON as a collapsible syntax-highlighted tree and can return to raw text', async () => {
    render(<SearchableContentViewer title="Raw request payload" content={'{"name":"search"}'} />)
    expect(screen.getByRole('region', { name: 'Formatted JSON' })).toBeTruthy()
    expect(screen.getByText('"name"').className).toContain('syntax-key')
    expect(screen.getByText('"search"').className).toContain('syntax-string')
    const formatter = screen.getByRole('checkbox', { name: 'Format JSON' }) as HTMLInputElement
    expect(formatter.checked).toBe(true)
    await userEvent.click(screen.getByText('Format JSON'))
    expect(formatter.checked).toBe(false)
    expect(screen.getByText('{"name":"search"}')).toBeTruthy()
  })

  it('collapses and expands nested JSON scopes', async () => {
    render(<SearchableContentViewer title="Raw request payload" content={'{"meta":{"enabled":true}}'} />)
    expect(screen.getByText('true').className).toContain('syntax-boolean')
    await userEvent.click(screen.getByRole('button', { name: 'Collapse meta' }))
    expect(screen.queryByText('true')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Expand meta' }))
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('explains when plain text cannot be formatted', () => {
    render(<SearchableContentViewer title="Block content" content="plain text" />)
    const formatter = screen.getByRole('checkbox', { name: 'Format JSON' }) as HTMLInputElement
    expect(formatter.disabled).toBe(true)
    expect(formatter.checked).toBe(false)
  })
})

describe('content helpers', () => {
  it('formats valid JSON and leaves other text untouched', () => {
    expect(formattedContent('{"ok":true}')).toEqual({ formatted: '{\n  "ok": true\n}', canFormat: true })
    expect(formattedContent('{}')).toEqual({ formatted: '{}', canFormat: true })
    expect(formattedContent('plain text')).toEqual({ formatted: 'plain text', canFormat: false })
  })

  it('returns non-overlapping text matches', () => {
    expect(findTextMatches('One one ONE', 'one')).toEqual([{ start: 0, end: 3 }, { start: 4, end: 7 }, { start: 8, end: 11 }])
  })
})
