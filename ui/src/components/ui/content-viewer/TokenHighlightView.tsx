import type { TokenWindowResponse } from '../../../api/client'
import type { BaseTextRange, TextMatch } from '../../../lib/textRanges'
import { PlainTextView } from './PlainTextView'
import type { SearchMarkRefs } from './types'

const TOKEN_COLORS = Array.from({ length: 10 }, (_, index) => `var(--token-highlight-${index + 1})`)

function tokenRanges(window: TokenWindowResponse | null): BaseTextRange[] {
  if (!window) return []
  let tokenIndex = 0
  return window.segments.map((segment) => {
    const range = { start: segment.start, end: segment.end, background: TOKEN_COLORS[tokenIndex % TOKEN_COLORS.length], className: 'rounded-[2px]' }
    tokenIndex += segment.token_count
    return range
  })
}

export function TokenHighlightView({ text, window, matches, activeIndex, markRefs }: {
  text: string
  window: TokenWindowResponse | null
  matches: TextMatch[]
  activeIndex: number
  markRefs: SearchMarkRefs
}) {
  return <PlainTextView text={text} baseRanges={tokenRanges(window)} matches={matches} activeIndex={activeIndex} markRefs={markRefs} />
}
