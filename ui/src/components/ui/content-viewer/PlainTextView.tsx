import type { BaseTextRange, TextMatch } from '../../../lib/textRanges'
import { DecoratedText } from './DecoratedText'
import type { SearchMarkRefs } from './types'

export function PlainTextView({ text, matches, activeIndex, markRefs, baseRanges = [] }: {
  text: string
  matches: TextMatch[]
  activeIndex: number
  markRefs: SearchMarkRefs
  baseRanges?: BaseTextRange[]
}) {
  return <pre className="min-w-0 [overflow-wrap:anywhere] [white-space:pre-wrap]"><DecoratedText text={text} baseRanges={baseRanges} matches={matches} activeIndex={activeIndex} markRefs={markRefs} /></pre>
}
