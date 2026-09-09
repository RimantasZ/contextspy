export interface TextMatch {
  start: number
  end: number
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

export function formattedContent(content: string): { formatted: string; canFormat: boolean } {
  try {
    return { formatted: JSON.stringify(JSON.parse(content), null, 2), canFormat: true }
  } catch {
    return { formatted: content, canFormat: false }
  }
}
