import { useEffect, useRef, useState } from 'react'
import type { RequestBlock } from '../../api/client'
import { tokenizeApi } from '../../api/client'
import { BLOCK_VISUALS, blockLabel, visualOf } from '../../lib/blockVisuals'

const TOKEN_COLORS = Array.from({ length: 7 }, (_, index) => `var(--token-highlight-${index + 1})`)

export function BlockInspector({ block, onJump, onClear }: {
  block: RequestBlock | null
  onJump: (targetId: number) => void
  onClear: () => void
}) {
  const cache = useRef(new Map<number, string[]>())
  const [highlight, setHighlight] = useState(false)
  const [tokens, setTokens] = useState<string[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setTokens(block ? (cache.current.get(block.id) ?? null) : null)
  }, [block])

  useEffect(() => {
    if (!highlight || !block?.content || block.content_purged || cache.current.has(block.id)) return
    let current = true
    setLoading(true)
    tokenizeApi.tokenize([block.content])
      .then((result) => {
        if (!current) return
        const next = result.results[0] ?? []
        cache.current.set(block.id, next)
        setTokens(next)
      })
      .catch(() => { if (current) setTokens(null) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [block, highlight])

  if (!block) {
    return (
      <aside className="panel-elevated flex min-h-48 items-center justify-center text-center text-sm text-[var(--text-muted)]" aria-label="Block inspector">
        Select a block to inspect its content and relationships.
      </aside>
    )
  }

  const visual = visualOf(block)
  const links = [
    { id: block.linked_previous_message_id, label: 'Previous message' },
    { id: block.linked_call_id, label: 'Tool call' },
    { id: block.linked_definition_id, label: 'Tool definition' },
  ].filter((link): link is { id: number; label: string } => link.id != null)

  return (
    <aside className="panel-elevated min-w-0 self-start overflow-hidden lg:sticky lg:top-4" aria-label="Block inspector">
      <div className="mb-3 flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <span
            className="mb-1 inline-flex rounded border border-[var(--graphical-border)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--block-ink)]"
            style={{ background: BLOCK_VISUALS[visual].color, borderColor: BLOCK_VISUALS[visual].border }}
          >
            {BLOCK_VISUALS[visual].short} · {BLOCK_VISUALS[visual].label}
          </span>
          <h3 className="truncate text-sm font-semibold" title={blockLabel(block)}>{blockLabel(block)}</h3>
        </div>
        <button type="button" onClick={onClear} className="app-button h-8 w-8 shrink-0 px-0" aria-label="Close block inspector">×</button>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div><dt className="text-[var(--text-muted)]">Tokens</dt><dd className="font-medium tabular-nums">{block.token_count.toLocaleString()}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Position</dt><dd className="font-medium tabular-nums">{block.position + 1}</dd></div>
        <div><dt className="text-[var(--text-muted)]">Message</dt><dd className="font-medium">{block.message_index ?? 'Structural'}</dd></div>
        <div><dt className="text-[var(--text-muted)]">First seen</dt><dd className="font-medium">{block.first_seen_session_seq != null ? `Request #${block.first_seen_session_seq}` : '—'}</dd></div>
        <div className="col-span-2"><dt className="text-[var(--text-muted)]">Tool</dt><dd className="break-all font-medium">{block.tool_name ?? '—'}</dd></div>
        <div className="col-span-2"><dt className="text-[var(--text-muted)]">Content state</dt><dd className="font-medium">{block.content_purged ? 'Purged by retention policy' : block.content == null ? 'No content captured' : 'Available'}</dd></div>
      </dl>

      {links.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-[var(--border)] pt-3">
          {links.map((link) => (
            <button key={link.label} type="button" onClick={() => onJump(link.id)} className="app-button min-h-8 py-1 text-xs" aria-label={`Jump to ${link.label.toLowerCase()}`}>
              {link.label} →
            </button>
          ))}
        </div>
      )}

      <div className="surface-inset mt-3 min-w-0 overflow-hidden rounded-md border border-[var(--border)]">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-2.5 py-2">
          <span className="text-xs font-medium">Content</span>
          {block.content && !block.content_purged && (
            <label className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
              <input type="checkbox" checked={highlight} onChange={(event) => setHighlight(event.target.checked)} />
              Token highlight
            </label>
          )}
        </div>
        <div className="max-h-[440px] min-h-28 overflow-auto p-3 font-mono text-xs leading-5 [overflow-wrap:anywhere] [white-space:pre-wrap]">
          {block.content_purged ? (
            <span className="italic text-[var(--text-muted)]">Content was purged, but its structure and token count are retained.</span>
          ) : block.content == null ? (
            <span className="italic text-[var(--text-muted)]">No content was captured for this structural block.</span>
          ) : highlight && loading ? (
            <span className="italic text-[var(--text-muted)]">Tokenizing selected block…</span>
          ) : highlight && tokens ? (
            tokens.map((token, index) => <span key={index} className="rounded-[2px]" style={{ background: TOKEN_COLORS[index % TOKEN_COLORS.length] }}>{token}</span>)
          ) : block.content}
        </div>
      </div>
    </aside>
  )
}
