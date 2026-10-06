import type { PurposeDetail, Request } from '../api/client'

const PURPOSE_LABELS: Record<string, string> = {
  user_turn: 'User turn',
  tool_continuation: 'Tool continuation',
  compaction: 'Compaction',
  housekeeping: 'Housekeeping',
  unknown: 'Unknown purpose',
}

/** Label for the inferred purpose; null when the request was never classified. */
export function purposeLabel(purpose: string | null): string | null {
  if (purpose == null) return null
  return PURPOSE_LABELS[purpose] ?? purpose
}

function describeResponse(response: NonNullable<PurposeDetail['response']>): string {
  const tools = response.tool_calls?.join(', ')
  switch (response.kind) {
    case 'tool_calls': return tools ? `calls ${tools}` : 'calls tools'
    case 'mixed': return tools ? `text and calls ${tools}` : 'text and tool calls'
    case 'final_text': return 'final answer'
    case 'empty': return 'no visible output'
    default: return String(response.kind)
  }
}

/** One line built from the detail the API returned, e.g. "results from Read, Grep → calls Edit". */
export function purposeSummary(request: Pick<Request, 'purpose_detail'>): string | null {
  const detail = request.purpose_detail
  if (!detail) return null
  const parts: string[] = []
  if (detail.trailing_tool_results?.length) parts.push(`results from ${detail.trailing_tool_results.join(', ')}`)
  if (detail.response) parts.push(describeResponse(detail.response))
  return parts.length > 0 ? parts.join(' → ') : null
}
