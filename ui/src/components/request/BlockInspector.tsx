import type { RequestBlock } from '../../api/client'
import { BLOCK_VISUALS, blockLabel, visualOf } from '../../lib/blockVisuals'

const TOOL_VISUAL_ORDER = { tool_definition: 0, tool_call: 1, tool_result: 2 } as const

function isToolBlock(block: RequestBlock): boolean {
  return block.block_type === 'tool_definition' || block.block_type === 'tool_call' || block.block_type === 'tool_result'
}

function BlockCue({ block, selected, onJump }: {
  block: RequestBlock
  selected: boolean
  onJump: (targetId: number) => void
}) {
  const visual = visualOf(block)
  const style = BLOCK_VISUALS[visual]
  const content = (
    <>
      <span className="shrink-0">{style.short}</span>
      <span className="min-w-0 flex-1 truncate text-left">{block.tool_name ?? style.label}</span>
      <span className="shrink-0 tabular-nums opacity-70">{block.token_count.toLocaleString()}</span>
    </>
  )
  const shared = `composition-block flex h-[26px] w-full min-w-0 items-center gap-1.5 overflow-hidden px-2 ${block.token_count <= 0 ? 'composition-block-zero' : ''}`
  const cueStyle = { backgroundColor: style.color, borderColor: style.border }

  return selected ? (
    <div className={shared} style={cueStyle} aria-label={`Selected ${blockLabel(block)}`}>{content}</div>
  ) : (
    <button type="button" className={shared} style={cueStyle} onClick={() => onJump(block.id)} aria-label={`Jump to ${blockLabel(block)}`}>
      {content}
    </button>
  )
}

export function BlockInspector({ block, blocks, onJump, onClear }: {
  block: RequestBlock | null
  blocks: RequestBlock[]
  onJump: (targetId: number) => void
  onClear: () => void
}) {
  if (!block) {
    return (
      <aside className="panel-elevated flex min-h-48 items-center justify-center text-center text-sm text-[var(--text-muted)]" aria-label="Block inspector">
        Select a block to inspect its metadata and relationships.
      </aside>
    )
  }

  const linkedIds = [block.linked_definition_id, block.linked_call_id].filter((id): id is number => id != null)
  if (block.block_type === 'tool_call') {
    const result = blocks.find((candidate) => candidate.linked_call_id === block.id)
    if (result) linkedIds.push(result.id)
  }
  const toolFlow = [block, ...linkedIds.map((id) => blocks.find((candidate) => candidate.id === id))]
    .filter((candidate): candidate is RequestBlock => candidate != null && isToolBlock(candidate))
    .filter((candidate, index, values) => values.findIndex((other) => other.id === candidate.id) === index)
    .sort((a, b) => TOOL_VISUAL_ORDER[visualOf(a) as keyof typeof TOOL_VISUAL_ORDER] - TOOL_VISUAL_ORDER[visualOf(b) as keyof typeof TOOL_VISUAL_ORDER])

  return (
    <aside className="panel-elevated min-w-0 self-start overflow-hidden lg:sticky lg:top-4" aria-label="Block inspector">
      <div className="mb-3 flex min-w-0 items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Selected block</h3>
        <button type="button" onClick={onClear} className="app-button h-8 w-8 shrink-0 px-0" aria-label="Close block inspector">×</button>
      </div>

      {!isToolBlock(block) && <BlockCue block={block} selected onJump={onJump} />}

      <dl className={`${isToolBlock(block) ? '' : 'mt-3'} grid grid-cols-2 gap-x-3 gap-y-2 text-xs`}>
        <div><dt className="text-[var(--text-muted)]">Type</dt><dd className="font-medium">{BLOCK_VISUALS[visualOf(block)].label}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Tokens</dt><dd className="font-medium tabular-nums">{block.token_count.toLocaleString()}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Position</dt><dd className="font-medium tabular-nums">{block.position + 1}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Message</dt><dd className="font-medium">{block.message_index ?? 'Structural'}</dd></div>
        <div><dt className="text-[var(--text-muted)]">First seen</dt><dd className="font-medium">{block.first_seen_session_seq != null ? `Request #${block.first_seen_session_seq}` : '—'}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Content</dt><dd className="font-medium">{block.content_purged ? 'Purged' : block.content == null ? 'Not captured' : 'Available'}</dd></div>
        {block.tool_call_id && <div className="col-span-2"><dt className="text-[var(--text-muted)]">Tool call ID</dt><dd className="truncate font-mono text-[11px]" title={block.tool_call_id}>{block.tool_call_id}</dd></div>}
      </dl>

      {toolFlow.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-[var(--border)] pt-3">
          <h4 className="text-xs font-semibold">Tool relationship</h4>
          {toolFlow.map((related, index) => (
            <div key={related.id}>
              {index > 0 && <div className="py-0.5 pl-3 text-xs text-[var(--text-subtle)]" aria-hidden="true">↓</div>}
              <div className="mb-1 text-[10px] text-[var(--text-muted)]">{BLOCK_VISUALS[visualOf(related)].label}</div>
              <BlockCue block={related} selected={related.id === block.id} onJump={onJump} />
            </div>
          ))}
        </div>
      )}

      {block.linked_previous_message_id != null && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <button type="button" onClick={() => onJump(block.linked_previous_message_id!)} className="app-button min-h-8 w-full py-1 text-xs" aria-label="Jump to previous message">
            Previous message →
          </button>
        </div>
      )}
    </aside>
  )
}
