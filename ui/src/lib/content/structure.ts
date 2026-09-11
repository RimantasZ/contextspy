import type { StructuredNode } from './types'

function indentation(line: string): number {
  const leading = line.match(/^[ \t]*/)?.[0] ?? ''
  return [...leading].reduce((total, char) => total + (char === '\t' ? 4 : 1), 0)
}

export function buildIndentationTree(content: string): StructuredNode[] {
  const roots: StructuredNode[] = []
  const stack: Array<{ indent: number; node: StructuredNode }> = []
  let nextId = 0

  content.split('\n').forEach((text) => {
    const node: StructuredNode = { id: nextId++, text, children: [] }
    if (!text.trim()) {
      if (stack.length > 0) stack[stack.length - 1].node.children.push(node)
      else roots.push(node)
      return
    }
    const level = indentation(text)
    while (stack.length > 0 && level <= stack[stack.length - 1].indent) stack.pop()
    if (stack.length > 0) stack[stack.length - 1].node.children.push(node)
    else roots.push(node)
    stack.push({ indent: level, node })
  })
  return roots
}

export function buildTomlTree(content: string): StructuredNode[] {
  const roots: StructuredNode[] = []
  let section: StructuredNode | null = null
  let nextId = 0
  content.split('\n').forEach((text) => {
    const node: StructuredNode = { id: nextId++, text, children: [] }
    if (/^\s*\[\[?.+\]?\]\s*$/.test(text)) {
      roots.push(node)
      section = node
    } else if (section) section.children.push(node)
    else roots.push(node)
  })
  return roots
}
