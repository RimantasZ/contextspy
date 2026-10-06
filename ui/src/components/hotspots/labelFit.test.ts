import { describe, expect, it } from 'vitest'
import { barPercent, labelPlacement, sharePct } from './labelFit'

describe('labelFit', () => {
  it('puts the label inside a bar that holds it and outside one that does not', () => {
    expect(labelPlacement(100, 300, 400)).toBe('inside')
    expect(labelPlacement(100, 60, 400)).toBe('outside')
  })
  it('keeps a long label inside when the bar leaves more room than the rest of the track', () => {
    expect(labelPlacement(500, 300, 400)).toBe('inside')
    expect(labelPlacement(500, 150, 400)).toBe('outside')
  })
  it('scales to the largest value and handles empty input', () => {
    expect(barPercent(25, 100)).toBe(25)
    expect(barPercent(0, 100)).toBe(0)
    expect(barPercent(5, 0)).toBe(0)
    expect(sharePct(8, 1000)).toBe(0.8)
    expect(sharePct(1, 0)).toBe(0)
  })
})
