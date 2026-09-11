import { useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { findTextMatches } from '../../../lib/textRanges'

export function useContentSearch({ text, modeKey, structured, countStructuredMatches, contentRootRef }: {
  text: string
  modeKey: string
  structured: boolean
  countStructuredMatches: (query: string) => number
  contentRootRef: RefObject<HTMLDivElement | null>
}) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const markRefs = useRef<Array<HTMLElement | null>>([])
  const matches = useMemo(() => findTextMatches(text, query), [text, query])
  const matchCount = structured ? countStructuredMatches(query) : matches.length

  useEffect(() => { setActiveIndex(0); markRefs.current = [] }, [text, modeKey, query])
  useEffect(() => {
    if (structured) {
      const marks = [...(contentRootRef.current?.querySelectorAll<HTMLElement>('[data-structured-search-match]') ?? [])]
      marks.forEach((mark, index) => mark.classList.toggle('search-match-active', index === activeIndex))
      marks[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
    } else markRefs.current[activeIndex]?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
  }, [activeIndex, contentRootRef, matchCount, structured])

  function moveMatch(delta: number) {
    if (matchCount === 0) return
    setActiveIndex((current) => (current + delta + matchCount) % matchCount)
  }
  return { query, setQuery, activeIndex, matches, matchCount, markRefs, moveMatch }
}
