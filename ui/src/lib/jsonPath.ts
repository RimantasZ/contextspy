const IDENTIFIER = /^[A-Za-z_$][A-Za-z0-9_$]*$/

/** Render a typed JSON path as readable text: ["messages", 3, "content"] -> messages[3].content */
export function formatJsonPath(path: readonly (string | number)[]): string {
  return path.reduce<string>((text, segment, index) => {
    if (typeof segment === 'number') return `${text}[${segment}]`
    if (IDENTIFIER.test(segment)) return index === 0 ? segment : `${text}.${segment}`
    return `${text}[${JSON.stringify(segment)}]`
  }, '')
}
