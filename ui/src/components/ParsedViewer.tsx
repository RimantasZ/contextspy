// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useState, useEffect, useCallback } from 'react'
import { tokenizeApi } from '../api/client'

// ---------------------------------------------------------------------------
// Token color palette — cycles per token index
// ---------------------------------------------------------------------------
const TOKEN_COLORS = [
  'rgba(99,102,241,0.32)',
  'rgba(52,211,153,0.25)',
  'rgba(251,191,36,0.28)',
  'rgba(239,68,68,0.22)',
  'rgba(56,189,248,0.25)',
  'rgba(167,139,250,0.28)',
  'rgba(251,146,60,0.25)',
  'rgba(34,197,94,0.22)',
  'rgba(244,114,182,0.22)',
  'rgba(20,184,166,0.25)',
]

// ---------------------------------------------------------------------------
// Category styles
// ---------------------------------------------------------------------------
type Category = 'system' | 'tool_def' | 'user' | 'assistant' | 'tool_result' | 'other'

const CAT_BAR: Record<Category, string> = {
  system:      'bg-purple-500',
  tool_def:    'bg-amber-500',
  user:        'bg-blue-500',
  assistant:   'bg-emerald-500',
  tool_result: 'bg-teal-500',
  other:       'bg-gray-500',
}
const CAT_LABEL: Record<Category, string> = {
  system:      'text-purple-300',
  tool_def:    'text-amber-300',
  user:        'text-blue-300',
  assistant:   'text-emerald-300',
  tool_result: 'text-teal-300',
  other:       'text-gray-400',
}

// Solid bg + left-border colours for the overview rectangles
const CAT_BG: Record<Category, string> = {
  system:      'rgba(168,85,247,0.18)',
  tool_def:    'rgba(245,158,11,0.18)',
  user:        'rgba(59,130,246,0.18)',
  assistant:   'rgba(16,185,129,0.18)',
  tool_result: 'rgba(20,184,166,0.18)',
  other:       'rgba(107,114,128,0.14)',
}
const CAT_BORDER: Record<Category, string> = {
  system:      'rgba(168,85,247,0.55)',
  tool_def:    'rgba(245,158,11,0.55)',
  user:        'rgba(59,130,246,0.55)',
  assistant:   'rgba(16,185,129,0.55)',
  tool_result: 'rgba(20,184,166,0.55)',
  other:       'rgba(107,114,128,0.45)',
}

function shortLabel(label: string): string {
  if (label.startsWith('Tool: ')) return label.slice(6)
  if (label.startsWith('Tool Result: ')) return label.slice(13)
  if (label === 'System Prompt') return 'System'
  const a = label.match(/^Assistant \(msg (\d+)\)$/)
  if (a) return `Asst. ${a[1]}`
  const u = label.match(/^User \(msg (\d+)\)$/)
  if (u) return `User ${u[1]}`
  const tr = label.match(/^Tool Result \(msg (\d+)\)$/)
  if (tr) return `Result ${tr[1]}`
  return label
}

// ---------------------------------------------------------------------------
// Context overview — contribution-graph-style justified block layout
// ---------------------------------------------------------------------------
interface OvBlock extends ParsedBlock { tokenCount: number }

function packRows(blocks: OvBlock[], tokensPerRow: number): OvBlock[][] {
  const rows: OvBlock[][] = []
  let current: OvBlock[] = []
  let rowTotal = 0
  for (const b of blocks) {
    if (rowTotal + b.tokenCount > tokensPerRow && current.length > 0) {
      rows.push(current); current = [b]; rowTotal = b.tokenCount
    } else {
      current.push(b); rowTotal += b.tokenCount
    }
  }
  if (current.length > 0) rows.push(current)
  return rows
}

function ContextOverview({
  blocks, tokenCounts, tokensList, selectedId, onSelect,
}: {
  blocks: ParsedBlock[]
  tokenCounts: number[]
  tokensList?: (string[] | null)[] | null
  selectedId: string | null
  onSelect: (id: string | null) => void
}) {
  const ovBlocks: OvBlock[] = blocks.map((b, i) => ({
    ...b, tokenCount: Math.max(tokenCounts[i] ?? 1, 1),
  }))
  const totalTokens = ovBlocks.reduce((s, b) => s + b.tokenCount, 0)
  if (totalTokens === 0) return null
  const tokensPerRow = Math.max(Math.ceil(totalTokens / 6), 200)
  const rows = packRows(ovBlocks, tokensPerRow)
  const selectedBlockIndex = selectedId ? ovBlocks.findIndex(b => b.id === selectedId) : -1
  const selectedBlock = selectedBlockIndex >= 0 ? ovBlocks[selectedBlockIndex] : null
  const selectedTokens = selectedBlockIndex >= 0 && tokensList ? tokensList[selectedBlockIndex] ?? null : null

  return (
    <div>
      <div className="p-3 space-y-1">
        {rows.map((row, ri) => (
          <div key={ri} className="flex gap-1" style={{ height: 52 }}>
            {row.map(b => (
              <button
                key={b.id}
                onClick={() => onSelect(selectedId === b.id ? null : b.id)}
                style={{
                  flex: `${Math.max(20, Math.sqrt(b.tokenCount))} 1 0`,
                  minWidth: 0,
                  background: selectedId === b.id
                    ? CAT_BORDER[b.category]
                    : CAT_BG[b.category],
                  borderLeft: `3px solid ${CAT_BORDER[b.category]}`,
                  outline: selectedId === b.id ? `2px solid ${CAT_BORDER[b.category]}` : 'none',
                  outlineOffset: '1px',
                }}
                className="rounded-sm px-2 py-1.5 overflow-hidden text-left transition-all hover:brightness-125 cursor-pointer"
                title={`${b.label}: ${b.tokenCount.toLocaleString()} tokens`}
              >
                <div className={`text-xs font-medium truncate leading-tight ${selectedId === b.id ? 'text-white' : CAT_LABEL[b.category]}`}>
                  <span className="tabular-nums mr-1">[{b.tokenCount.toLocaleString()}]</span>{shortLabel(b.label)}
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>

      {selectedBlock && (
        <div className="border-t border-gray-700 mx-3 mb-3">
          <div
            className="flex items-center justify-between px-3 py-2 rounded-t"
            style={{ background: CAT_BG[selectedBlock.category], borderLeft: `3px solid ${CAT_BORDER[selectedBlock.category]}` }}
          >
            <span className={`text-xs font-medium ${CAT_LABEL[selectedBlock.category]}`}>
              {selectedBlock.label}
            </span>
            <div className="flex items-center gap-3">
              <span className="text-[10px] text-gray-500 tabular-nums">
                {selectedBlock.tokenCount.toLocaleString()} tokens
              </span>
              <button
                onClick={() => onSelect(null)}
                className="text-gray-500 hover:text-gray-300 text-xs leading-none"
              >
                ✕
              </button>
            </div>
          </div>
          <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-words max-h-[320px] overflow-auto bg-gray-900 px-3 py-2.5 rounded-b border border-gray-700 border-t-0 leading-6">
            {selectedTokens ? (
              selectedTokens.map((tok, j) => (
                <span
                  key={j}
                  style={{ background: TOKEN_COLORS[j % TOKEN_COLORS.length] }}
                  className="rounded-[2px] text-gray-100"
                >
                  {tok}
                </span>
              ))
            ) : (
              selectedBlock.text
            )}
          </pre>
        </div>
      )}
    </div>
  )
}
interface ParsedBlock {
  id: string
  label: string
  category: Category
  text: string
}

function contentToStr(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content.map((b: unknown) => {
    if (typeof b === 'string') return b
    const block = b as Record<string, unknown>
    if (block.type === 'text') return block.text as string || ''
    if (block.type === 'tool_use') return `[Tool call: ${block.name}]\n${JSON.stringify(block.input, null, 2)}`
    if (block.type === 'tool_result') {
      const inner = Array.isArray(block.content)
        ? (block.content as {text?: string}[]).map(x => x.text ?? '').join('\n')
        : (typeof block.content === 'string' ? block.content : '')
      return inner
    }
    return JSON.stringify(b)
  }).filter(Boolean).join('\n')
}

function extractBlocks(rawBody: string): ParsedBlock[] {
  try {
    const body = JSON.parse(rawBody) as Record<string, unknown>
    const blocks: ParsedBlock[] = []

    // System (Anthropic top-level)
    if (body.system) {
      const text = contentToStr(body.system)
      if (text) blocks.push({ id: 'system', label: 'System Prompt', category: 'system', text })
    }

    // Tool definitions
    const tools = (body.tools as unknown[] | undefined) ?? (body.functions as unknown[] | undefined) ?? []
    for (const tool of tools) {
      const t = tool as Record<string, unknown>
      const name = (t.name as string) ?? (t.function as Record<string, unknown>)?.name as string ?? 'unknown'
      blocks.push({
        id: `tool-${name}`,
        label: `Tool: ${name}`,
        category: 'tool_def',
        text: JSON.stringify(tool, null, 2),
      })
    }

    // Messages
    const messages = (body.messages as unknown[] | undefined) ?? []
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i] as Record<string, unknown>
      const role = (msg.role as string) ?? 'unknown'
      const content = msg.content

      if (role === 'system') {
        const text = contentToStr(content)
        if (text) blocks.push({ id: `msg-${i}`, label: 'System Prompt', category: 'system', text })
        continue
      }

      // Tool result message (OpenAI role=tool, or Anthropic user with tool_result blocks)
      const isOaiToolResult = role === 'tool'
      const isAnthropicToolResult = Array.isArray(content) &&
        (content as {type?: string}[]).some(b => b.type === 'tool_result')

      if (isOaiToolResult || isAnthropicToolResult) {
        let text = ''
        const toolName = msg.name as string | undefined
        if (isOaiToolResult) {
          text = contentToStr(content)
        } else {
          // Extract each tool_result block separately
          for (const block of (content as {type?: string; content?: unknown; tool_use_id?: string}[])) {
            if (block.type === 'tool_result') {
              const inner = Array.isArray(block.content)
                ? (block.content as {text?: string}[]).map(x => x.text ?? '').join('\n')
                : (typeof block.content === 'string' ? block.content : '')
              if (inner) text += (text ? '\n---\n' : '') + inner
            }
          }
        }
        if (text) blocks.push({
          id: `msg-${i}`,
          label: toolName ? `Tool Result: ${toolName}` : `Tool Result (msg ${i + 1})`,
          category: 'tool_result',
          text,
        })
        continue
      }

      // Assistant with tool_calls (OpenAI) or tool_use blocks (Anthropic)
      if (role === 'assistant') {
        const parts: string[] = []
        if (Array.isArray(content)) {
          for (const b of content as {type?: string; text?: string; name?: string; input?: unknown}[]) {
            if (b.type === 'text' && b.text) parts.push(b.text)
            else if (b.type === 'tool_use') parts.push(`[Tool call: ${b.name}]\n${JSON.stringify(b.input, null, 2)}`)
          }
        } else if (typeof content === 'string' && content) {
          parts.push(content)
        }
        // OpenAI tool_calls field
        for (const tc of (msg.tool_calls as {function?: {name?: string; arguments?: string}}[] | undefined) ?? []) {
          parts.push(`[Tool call: ${tc.function?.name}]\n${tc.function?.arguments ?? ''}`)
        }
        const text = parts.filter(Boolean).join('\n\n')
        if (text) blocks.push({ id: `msg-${i}`, label: `Assistant (msg ${i + 1})`, category: 'assistant', text })
        continue
      }

      // Regular user message
      const text = contentToStr(content)
      if (text) blocks.push({
        id: `msg-${i}`,
        label: `${role.charAt(0).toUpperCase() + role.slice(1)} (msg ${i + 1})`,
        category: role === 'user' ? 'user' : 'other',
        text,
      })
    }

    return blocks
  } catch {
    return []
  }
}

// ---------------------------------------------------------------------------
// Block component — shows tokenized text with colored spans
// ---------------------------------------------------------------------------
interface BlockProps {
  block: ParsedBlock
  tokens: string[] | null
}

function TokenBlock({ block, tokens }: BlockProps) {
  const [collapsed, setCollapsed] = useState(true)
  const tokenCount = tokens?.length ?? null

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-750 text-left"
      >
        <span className={`w-1 self-stretch rounded-full shrink-0 ${CAT_BAR[block.category]}`} />
        <span className={`text-xs font-medium ${CAT_LABEL[block.category]} flex-1`}>{block.label}</span>
        {tokenCount !== null && (
          <span className="text-xs text-gray-500 tabular-nums">{tokenCount.toLocaleString()} tokens</span>
        )}
        <span className="text-gray-600 text-xs ml-1">{collapsed ? '▶' : '▼'}</span>
      </button>
      {!collapsed && (
        <div className="px-3 py-2.5 bg-gray-900 text-xs font-mono leading-relaxed overflow-auto max-h-[400px]">
          {tokens === null ? (
            <span className="text-gray-400 whitespace-pre-wrap break-words">{block.text}</span>
          ) : (
            <span className="whitespace-pre-wrap break-words leading-6">
              {tokens.map((tok, j) => (
                <span
                  key={j}
                  style={{ background: TOKEN_COLORS[j % TOKEN_COLORS.length] }}
                  className="rounded-[2px] text-gray-100"
                >
                  {tok}
                </span>
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main ParsedViewer
// ---------------------------------------------------------------------------
interface Props {
  rawBody: string | null | undefined
  rawContent?: string | null
  totalInputTokens?: number | null
}

export function ParsedViewer({ rawBody, rawContent, totalInputTokens }: Props) {
  const [tab, setTab] = useState<'overview' | 'parsed' | 'raw'>('overview')
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null)
  const [tokenized, setTokenized] = useState<string[][] | null>(null)
  const [loading, setLoading] = useState(false)
  const [showHighlight, setShowHighlight] = useState(true)
  const [colorizeOverview, setColorizeOverview] = useState(false)

  const blocks = rawBody ? extractBlocks(rawBody) : []

  const fetchTokens = useCallback(async () => {
    if (!rawBody || blocks.length === 0) return
    setLoading(true)
    try {
      const res = await tokenizeApi.tokenize(blocks.map(b => b.text))
      setTokenized(res.results)
    } catch {
      setTokenized(null)
    } finally {
      setLoading(false)
    }
  }, [rawBody]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setTokenized(null)
    setSelectedBlockId(null)
    fetchTokens()
  }, [fetchTokens])

  if (!rawBody) {
    return <p className="px-4 py-3 text-sm text-gray-500 italic">Raw content has been purged.</p>
  }

  if (blocks.length === 0) {
    return <p className="px-4 py-3 text-sm text-gray-500 italic">Cannot parse request body.</p>
  }

  const tokenCounts = tokenized ? blocks.map((_, i) => tokenized[i]?.length ?? 0) : null
  const totalTokens = tokenCounts ? tokenCounts.reduce((s, c) => s + c, 0) : null

  // Pretty-print for Raw tab
  let rawPretty = rawContent ?? rawBody ?? ''
  try { rawPretty = JSON.stringify(JSON.parse(rawPretty), null, 2) } catch { /* keep as-is */ }

  return (
    <div className="overflow-auto max-h-[700px]">
      {/* Top-level tab bar */}
      <div className="flex items-center justify-between border-b border-gray-800">
        <div className="flex">
          {(['overview', 'parsed', 'raw'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors capitalize ${
                tab === t
                  ? 'border-indigo-500 text-indigo-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {t === 'overview' ? 'Overview' : t === 'parsed' ? 'Parsed' : 'Raw'}
            </button>
          ))}
        </div>
        <div className="pr-3 flex items-center gap-3">
          {loading && <span className="text-xs text-gray-500 italic">Tokenizing…</span>}
          {!loading && (totalInputTokens != null || totalTokens !== null) && (
            <span className="text-xs text-gray-500 tabular-nums">
              {(totalInputTokens ?? totalTokens!).toLocaleString()} tokens
            </span>
          )}
          {tab === 'overview' && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={colorizeOverview}
                onChange={e => setColorizeOverview(e.target.checked)}
                className="accent-indigo-500"
              />
              Highlight tokens
            </label>
          )}
          {tab === 'parsed' && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showHighlight}
                onChange={e => setShowHighlight(e.target.checked)}
                className="accent-indigo-500"
              />
              Highlight tokens
            </label>
          )}
        </div>
      </div>

      {/* Overview tab */}
      {tab === 'overview' && (
        tokenCounts === null ? (
          <div className="p-4 text-xs text-gray-500 italic">
            {loading ? 'Tokenizing…' : 'No data available.'}
          </div>
        ) : (
          <ContextOverview
            blocks={blocks}
            tokenCounts={tokenCounts}
            tokensList={colorizeOverview ? tokenized : null}
            selectedId={selectedBlockId}
            onSelect={setSelectedBlockId}
          />
        )
      )}

      {/* Parsed tab */}
      {tab === 'parsed' && (
        <div className="p-3 space-y-2">
          {blocks.map((block, i) => (
            <TokenBlock
              key={block.id}
              block={block}
              tokens={showHighlight && tokenized ? tokenized[i] ?? null : null}
            />
          ))}
        </div>
      )}

      {/* Raw tab */}
      {tab === 'raw' && (
        <div className="p-4 overflow-auto max-h-[600px]">
          <pre className="text-xs font-mono text-gray-300 whitespace-pre-wrap break-all">
            {rawPretty}
          </pre>
        </div>
      )}
    </div>
  )
}
