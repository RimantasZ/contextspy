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

// ---------------------------------------------------------------------------
// Block extraction from raw request body JSON
// ---------------------------------------------------------------------------
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
  const [collapsed, setCollapsed] = useState(false)
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
}

export function ParsedViewer({ rawBody }: Props) {
  const [tokenized, setTokenized] = useState<string[][] | null>(null)
  const [loading, setLoading] = useState(false)

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
    fetchTokens()
  }, [fetchTokens])

  if (!rawBody) {
    return <p className="px-4 py-3 text-sm text-gray-500 italic">Raw content has been purged.</p>
  }

  if (blocks.length === 0) {
    return <p className="px-4 py-3 text-sm text-gray-500 italic">Cannot parse request body.</p>
  }

  return (
    <div className="p-3 space-y-2 overflow-auto max-h-[700px]">
      {loading && (
        <p className="text-xs text-gray-500 italic px-1 pb-1">Tokenizing…</p>
      )}
      {blocks.map((block, i) => (
        <TokenBlock
          key={block.id}
          block={block}
          tokens={tokenized ? tokenized[i] ?? null : null}
        />
      ))}
    </div>
  )
}
