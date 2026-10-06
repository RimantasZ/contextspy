import { describe, expect, it } from 'vitest'
import { formatBytes, formatElapsedDuration, formatPercent, formatRequestDuration } from './format'

describe('formatBytes', () => {
  it('uses binary units with one decimal', () => {
    expect(formatBytes(0)).toBe('0 bytes')
    expect(formatBytes(1023)).toBe('1,023 bytes')
    expect(formatBytes(1024)).toBe('1.0 KiB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MiB')
    expect(formatBytes(2.5 * 1024 ** 3)).toBe('2.5 GiB')
    expect(formatBytes(5 * 1024 ** 4)).toBe('5.0 TiB')
  })
})

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
