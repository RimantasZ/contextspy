export interface TextMatch {
  start: number
  end: number
}

export interface BaseTextRange {
  start: number
  end: number
  className?: string
  background?: string
}

export interface DecoratedTextRange extends BaseTextRange {
  matchIndex?: number
}

export function findTextMatches(text: string, query: string): TextMatch[] {
  const needle = query.toLocaleLowerCase()
  if (!needle) return []
  const haystack = text.toLocaleLowerCase()
  const matches: TextMatch[] = []
  let offset = 0
  while (offset <= haystack.length - needle.length) {
    const start = haystack.indexOf(needle, offset)
    if (start < 0) break
    matches.push({ start, end: start + needle.length })
    offset = start + Math.max(1, needle.length)
  }
  return matches
}

export function composeTextRanges(
  textLength: number,
  baseRanges: BaseTextRange[],
  matches: TextMatch[],
): DecoratedTextRange[] {
  if (textLength <= 0) return []
  const boundaries = new Set<number>([0, textLength])
  for (const range of [...baseRanges, ...matches]) {
    boundaries.add(Math.max(0, Math.min(textLength, range.start)))
    boundaries.add(Math.max(0, Math.min(textLength, range.end)))
  }
  const ordered = [...boundaries].sort((a, b) => a - b)
  const output: DecoratedTextRange[] = []

  for (let index = 0; index < ordered.length - 1; index += 1) {
    const start = ordered[index]
    const end = ordered[index + 1]
    if (end <= start) continue
    const base = baseRanges.find((range) => range.start <= start && range.end >= end)
    const matchIndex = matches.findIndex((match) => match.start <= start && match.end >= end)
    const range: DecoratedTextRange = {
      start,
      end,
      className: base?.className,
      background: base?.background,
      ...(matchIndex >= 0 ? { matchIndex } : {}),
    }
    const previous = output[output.length - 1]
    if (previous && previous.end === range.start && previous.className === range.className
      && previous.background === range.background && previous.matchIndex === range.matchIndex) {
      previous.end = range.end
    } else output.push(range)
  }
  return output
}
