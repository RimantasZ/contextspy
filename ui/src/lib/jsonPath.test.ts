import { describe, expect, it } from 'vitest'
import { formatJsonPath } from './jsonPath'

describe('formatJsonPath', () => {
  it('renders keys and indexes', () => {
    expect(formatJsonPath(['messages', 3, 'content', 1])).toBe('messages[3].content[1]')
    expect(formatJsonPath(['system'])).toBe('system')
    expect(formatJsonPath(['tools', 1, 'tools', 0])).toBe('tools[1].tools[0]')
  })

  it('quotes keys that are not identifiers', () => {
    expect(formatJsonPath(['input', 'a-b', 2])).toBe('input["a-b"][2]')
  })

  it('handles an empty path', () => {
    expect(formatJsonPath([])).toBe('')
  })
})
