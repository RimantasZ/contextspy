/** Where a row's label goes in the "on the bar" layout: inside the bar when it fits, else just after the bar. */
export type LabelPlacement = 'inside' | 'outside'

const INSIDE_PADDING = 12
/** Rough width of one character at the label's font size; only used where nothing can be measured (tests). */
export const ESTIMATED_CHAR_WIDTH = 6.5
export const ESTIMATED_TRACK_WIDTH = 600

/**
 * Pure decision so it can be tested without layout. All widths are in pixels; `barWidth` is the filled part
 * of the track. A label that does not fit goes outside, unless the bar is so long that more room is left inside.
 */
export function labelPlacement(textWidth: number, barWidth: number, trackWidth: number): LabelPlacement {
  if (textWidth + INSIDE_PADDING <= barWidth) return 'inside'
  return barWidth > trackWidth - barWidth ? 'inside' : 'outside'
}

/** Fraction (0-100) of the largest row's metric, never invisible for a non-zero value. */
export function barPercent(value: number, largest: number): number {
  if (largest <= 0 || value <= 0) return 0
  return Math.min(100, (value / largest) * 100)
}

/** Share of a whole, one decimal. */
export function sharePct(part: number, whole: number): number {
  return whole > 0 ? Math.round((1000 * part) / whole) / 10 : 0
}
