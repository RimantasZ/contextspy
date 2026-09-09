import type { RequestBlock } from '../api/client'

export type BlockVisual =
  | 'system'
  | 'tool_definition'
  | 'user'
  | 'assistant'
  | 'tool_call'
  | 'tool_result'
  | 'thinking'
  | 'prefill'
  | 'other'

export const BLOCK_VISUALS: Record<BlockVisual, { label: string; short: string; color: string }> = {
  system: { label: 'System', short: 'S', color: 'var(--block-system)' },
  tool_definition: { label: 'Tool definition', short: 'D', color: 'var(--block-tool-definition)' },
  user: { label: 'User', short: 'U', color: 'var(--block-user)' },
  assistant: { label: 'Assistant', short: 'A', color: 'var(--block-assistant)' },
  tool_call: { label: 'Tool call', short: 'C', color: 'var(--block-tool-call)' },
  tool_result: { label: 'Tool result', short: 'R', color: 'var(--block-tool-result)' },
  thinking: { label: 'Thinking', short: 'T', color: 'var(--block-thinking)' },
  prefill: { label: 'Assistant prefill', short: 'P', color: 'var(--block-prefill)' },
  other: { label: 'Other', short: 'O', color: 'var(--block-other)' },
}

const TYPE_TO_VISUAL: Record<string, BlockVisual> = {
  system_prompt: 'system',
  tool_definition: 'tool_definition',
  user_message: 'user',
  assistant_message: 'assistant',
  tool_call: 'tool_call',
  tool_result: 'tool_result',
  thinking: 'thinking',
  assistant_prefill: 'prefill',
}

export function visualOf(block: RequestBlock): BlockVisual {
  return TYPE_TO_VISUAL[block.block_type] ?? 'other'
}

export function blockLabel(block: RequestBlock): string {
  const base = BLOCK_VISUALS[visualOf(block)].label
  if (block.tool_name && (block.block_type === 'tool_call' || block.block_type === 'tool_result' || block.block_type === 'tool_definition')) {
    return `${base}: ${block.tool_name}`
  }
  if (block.message_index != null && block.block_type !== 'system_prompt' && block.block_type !== 'tool_definition') {
    return `${base} · message ${block.message_index}`
  }
  return base
}

export function blockAccessibleName(block: RequestBlock): string {
  const states = [
    `${block.token_count.toLocaleString()} tokens`,
    block.position != null ? `position ${block.position + 1}` : '',
    block.content_purged ? 'content purged' : '',
  ].filter(Boolean)
  return `${blockLabel(block)}, ${states.join(', ')}`
}

export function sortedBlocks(blocks: RequestBlock[]): RequestBlock[] {
  return [...blocks].sort((a, b) => a.position - b.position || a.id - b.id)
}
