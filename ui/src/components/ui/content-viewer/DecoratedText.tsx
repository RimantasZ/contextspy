import type { CSSProperties, ReactNode } from 'react'
import { composeTextRanges } from '../../../lib/textRanges'
import type { BaseTextRange, TextMatch } from '../../../lib/textRanges'
import type { SearchMarkRefs } from './types'

function BaseSpan({ text, range }: { text: string; range: BaseTextRange }) {
  const style: CSSProperties | undefined = range.background ? { background: range.background } : undefined
  if (!range.className && !style) return <>{text.slice(range.start, range.end)}</>
  return <span className={range.className} style={style}>{text.slice(range.start, range.end)}</span>
}

export function DecoratedText({ text, baseRanges = [], matches = [], activeIndex = 0, markRefs, structuredSearch = false }: {
  text: string
  baseRanges?: BaseTextRange[]
  matches?: TextMatch[]
  activeIndex?: number
  markRefs?: SearchMarkRefs
  structuredSearch?: boolean
}) {
  const ranges = composeTextRanges(text.length, baseRanges, matches)
  const nodes: ReactNode[] = []

  for (let index = 0; index < ranges.length;) {
    const range = ranges[index]
    if (range.matchIndex == null) {
      nodes.push(<BaseSpan key={`range-${range.start}`} text={text} range={range} />)
      index += 1
      continue
    }
    const matchIndex = range.matchIndex
    const matched: ReactNode[] = []
    while (index < ranges.length && ranges[index].matchIndex === matchIndex) {
      const part = ranges[index]
      matched.push(<BaseSpan key={`match-part-${part.start}`} text={text} range={part} />)
      index += 1
    }
    nodes.push(
      <mark
        key={`match-${matchIndex}-${range.start}`}
        ref={markRefs ? (node) => { markRefs.current[matchIndex] = node } : undefined}
        data-structured-search-match={structuredSearch ? true : undefined}
        className={`rounded-[2px] bg-[var(--search-mark)] text-[var(--search-mark-text)] ${matchIndex === activeIndex ? 'search-match-active' : ''}`}
      >
        {matched}
      </mark>,
    )
  }
  return nodes
}
