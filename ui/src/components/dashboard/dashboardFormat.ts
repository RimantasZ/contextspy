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
import type { DashboardActivityPoint, DashboardBlockChange } from '../../api/client'

export function parseServerTimestamp(timestamp: string): number {
  const utc = timestamp.endsWith('Z') || /[+-]\d\d:\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`
  return new Date(utc).getTime()
}

export function requestLabel(sessionSeq: number | null, id: string): string {
  return sessionSeq != null ? `#${sessionSeq}` : id.slice(0, 8)
}

export function formatCompactTokens(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${trimFraction(value / 1_000_000)}M`
  if (abs >= 10_000) return `${Math.round(value / 1000)}k`
  if (abs >= 1000) return `${trimFraction(value / 1000)}k`
  return String(value)
}

function trimFraction(value: number): string {
  return value.toFixed(1).replace(/\.0$/, '')
}

export function formatSignedTokens(delta: number): string {
  if (delta === 0) return '0'
  const magnitude = Math.abs(delta).toLocaleString()
  return delta > 0 ? `+${magnitude}` : `\u2212${magnitude}`
}

export function formatTokenTooltip(value: number, name: string): [string, string] {
  return [`${value.toLocaleString()} tokens`, name]
}

export interface ActivityChartDatum {
  label: string
  tokens_total_input: number
  tokens_total_output: number
}

export function toActivityChartData(activity: DashboardActivityPoint[]): ActivityChartDatum[] {
  return activity.map((point) => ({
    label: requestLabel(point.session_seq, point.id),
    tokens_total_input: point.tokens_total_input,
    tokens_total_output: point.tokens_total_output,
  }))
}

const BLOCK_TYPE_ORDER: Array<[string, string]> = [
  ['tool_call', 'Tool calls'],
  ['user_message', 'User messages'],
  ['tool_result', 'Tool results'],
  ['assistant_message', 'Assistant messages'],
  ['tool_definition', 'Tool definitions'],
  ['system_prompt', 'System prompts'],
  ['assistant_prefill', 'Assistant prefills'],
  ['thinking', 'Thinking blocks'],
  ['other', 'Other blocks'],
]

const MAX_BLOCK_ROWS = 4

export interface BlockChangeRow {
  blockType: string
  label: string
  delta: number
}

export function selectBlockChangeRows(changes: DashboardBlockChange[]): {
  rows: BlockChangeRow[]
  hiddenCount: number
} {
  const changed = new Map(changes.filter((c) => c.delta !== 0).map((c) => [c.block_type, c.delta]))
  const ordered: BlockChangeRow[] = []
  for (const [blockType, label] of BLOCK_TYPE_ORDER) {
    const delta = changed.get(blockType)
    if (delta !== undefined) ordered.push({ blockType, label, delta })
    changed.delete(blockType)
  }
  for (const [blockType, delta] of changed) {
    ordered.push({ blockType, label: blockType.replace(/_/g, ' '), delta })
  }
  return { rows: ordered.slice(0, MAX_BLOCK_ROWS), hiddenCount: Math.max(0, ordered.length - MAX_BLOCK_ROWS) }
}
