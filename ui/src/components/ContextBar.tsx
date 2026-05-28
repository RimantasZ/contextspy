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

export const CATEGORY_COLORS: Record<string, string> = {
  system_prompt: '#6366f1',
  tool_definitions: '#8b5cf6',
  tool_results: '#a78bfa',
  file_contents: '#22c55e',
  conversation_history: '#3b82f6',
  current_user_message: '#06b6d4',
  assistant_prefill: '#f59e0b',
  uncategorized: '#6b7280',
};

export const CATEGORY_LABELS: Record<string, string> = {
  system_prompt: 'System Prompt',
  tool_definitions: 'Tool Definitions',
  tool_results: 'Tool Results',
  file_contents: 'File Contents',
  conversation_history: 'Conversation History',
  current_user_message: 'User Message',
  assistant_prefill: 'Assistant Prefill',
  uncategorized: 'Uncategorized',
};

export const CATEGORY_ORDER = [
  'system_prompt', 'tool_definitions', 'tool_results', 'file_contents',
  'conversation_history', 'current_user_message', 'assistant_prefill', 'uncategorized',
];

export interface TokenCategories {
  tokens_system_prompt: number;
  tokens_tool_definitions: number;
  tokens_tool_results: number;
  tokens_file_contents: number;
  tokens_conversation_history: number;
  tokens_current_user_message: number;
  tokens_assistant_prefill: number;
  tokens_uncategorized: number;
}

export function ContextBar({ data }: { data: TokenCategories }) {
  const values: Record<string, number> = {
    system_prompt: data.tokens_system_prompt,
    tool_definitions: data.tokens_tool_definitions,
    tool_results: data.tokens_tool_results,
    file_contents: data.tokens_file_contents,
    conversation_history: data.tokens_conversation_history,
    current_user_message: data.tokens_current_user_message,
    assistant_prefill: data.tokens_assistant_prefill,
    uncategorized: data.tokens_uncategorized,
  };
  const total = Object.values(values).reduce((a, b) => a + b, 0);

  if (total === 0) {
    return (
      <div style={{ width: 240, height: 12, backgroundColor: '#1f2937' }} />
    );
  }

  const segments = CATEGORY_ORDER
    .filter(cat => values[cat] > 0)
    .map(cat => ({
      key: cat,
      pct: (values[cat] / total) * 100,
      tooltip: `${CATEGORY_LABELS[cat]}: ${values[cat].toLocaleString()} (${((values[cat] / total) * 100).toFixed(1)}%)`,
    }));

  return (
    <div style={{ display: 'flex', width: 240, height: 12, overflow: 'hidden', gap: 1 }}>
      {segments.map(seg => (
        <div
          key={seg.key}
          style={{
            width: `${seg.pct}%`,
            minWidth: 2,
            height: '100%',
            backgroundColor: CATEGORY_COLORS[seg.key],
          }}
          title={seg.tooltip}
        />
      ))}
    </div>
  );
}
