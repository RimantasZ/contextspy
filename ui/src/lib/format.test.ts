import { describe, expect, it } from 'vitest'
import { formatElapsedDuration, formatPercent, formatRequestDuration } from './format'

describe('shared value formatting', () => {
  it('formats request and elapsed durations consistently', () => {
    expect(formatRequestDuration(250)).toBe('250ms')
    expect(formatRequestDuration(1_250)).toBe('1.3s')
    expect(formatElapsedDuration(65_000)).toBe('1m 5s')
    expect(formatElapsedDuration(null, 'active')).toBe('active')
  })

  it('formats percentages only with a usable denominator', () => {
    expect(formatPercent(25, 200)).toBe('12.5%')
    expect(formatPercent(25, 0)).toBeNull()
    expect(formatPercent(25)).toBeNull()
  })
})
