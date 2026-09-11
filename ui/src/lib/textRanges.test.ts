import { describe, expect, it } from 'vitest'
import { composeTextRanges, findTextMatches } from './textRanges'

describe('text ranges', () => {
  it('finds non-overlapping case-insensitive matches', () => {
    expect(findTextMatches('One one ONE', 'one')).toEqual([
      { start: 0, end: 3 }, { start: 4, end: 7 }, { start: 8, end: 11 },
    ])
  })

  it('overlays a match across syntax boundaries without losing source text', () => {
    const text = 'const ready'
    const ranges = composeTextRanges(text.length, [
      { start: 0, end: 5, className: 'keyword' },
      { start: 5, end: text.length, className: 'plain' },
    ], [{ start: 3, end: 8 }])
    expect(ranges.map((range) => text.slice(range.start, range.end)).join('')).toBe(text)
    expect(ranges.filter((range) => range.matchIndex === 0).map((range) => text.slice(range.start, range.end)).join('')).toBe('st re')
  })

  it('handles empty and Unicode content', () => {
    expect(composeTextRanges(0, [], [])).toEqual([])
    const text = 'A😀B'
    const ranges = composeTextRanges(text.length, [], [{ start: 1, end: 3 }])
    expect(ranges.map((range) => text.slice(range.start, range.end)).join('')).toBe(text)
  })
})
