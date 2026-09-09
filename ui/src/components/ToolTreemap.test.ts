import { describe, expect, it } from 'vitest'
import { buildToolTreemapData, toolColor } from './ToolTreemap'

describe('tool treemap data', () => {
  it('uses stable name-based colors and splits definitions from results', () => {
    expect(toolColor('search')).toBe(toolColor('search'))
    expect(toolColor('search')).not.toBe(toolColor('terminal'))
    const [node] = buildToolTreemapData([{ tool_name: 'search', definition_tokens: 20, result_tokens: 80 }])
    expect(node.total).toBe(100)
    expect(node.children.map((child) => [child.name, child.value])).toEqual([['Definitions', 20], ['Results', 80]])
    expect(node.children[0].fill).not.toBe(node.children[1].fill)
  })

  it('groups tools below one percent into Other', () => {
    const data = buildToolTreemapData([
      { tool_name: 'large', definition_tokens: 1000, result_tokens: 0 },
      { tool_name: 'tiny-a', definition_tokens: 2, result_tokens: 0 },
      { tool_name: 'tiny-b', definition_tokens: 0, result_tokens: 3 },
    ])
    expect(data.map((node) => node.name)).toEqual(['large', 'Other'])
    expect(data[1]).toMatchObject({ total: 5, groupedCount: 2 })
  })
})
