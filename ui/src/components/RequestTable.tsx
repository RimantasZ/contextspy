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
import { useState } from 'react';
import type { Request, Session } from '../api/client';

const PROVIDER_COLORS: Record<string, string> = {
  openai: 'bg-green-900 text-green-300',
  anthropic: 'bg-orange-900 text-orange-300',
  ollama: 'bg-blue-900 text-blue-300',
  unknown: 'bg-gray-700 text-gray-400',
};

const AGENT_COLORS: Record<string, string> = {
  copilot: 'bg-purple-900 text-purple-300',
  claude: 'bg-orange-900 text-orange-300',
  cursor: 'bg-blue-900 text-blue-300',
  unknown: 'bg-gray-700 text-gray-400',
};

const CATEGORY_COLORS: Record<string, string> = {
  system_prompt: '#6366f1',
  tool_definitions: '#8b5cf6',
  tool_results: '#a78bfa',
  file_contents: '#22c55e',
  conversation_history: '#3b82f6',
  current_user_message: '#06b6d4',
  assistant_prefill: '#f59e0b',
  uncategorized: '#6b7280',
};

const CATEGORY_LABELS: Record<string, string> = {
  system_prompt: 'System Prompt',
  tool_definitions: 'Tool Definitions',
  tool_results: 'Tool Results',
  file_contents: 'File Contents',
  conversation_history: 'Conversation History',
  current_user_message: 'User Message',
  assistant_prefill: 'Assistant Prefill',
  uncategorized: 'Uncategorized',
};

const CATEGORY_ORDER = [
  'system_prompt', 'tool_definitions', 'tool_results', 'file_contents',
  'conversation_history', 'current_user_message', 'assistant_prefill', 'uncategorized',
];

function ContextBar({ req }: { req: Request }) {
  const values: Record<string, number> = {
    system_prompt: req.tokens_system_prompt,
    tool_definitions: req.tokens_tool_definitions,
    tool_results: req.tokens_tool_results,
    file_contents: req.tokens_file_contents,
    conversation_history: req.tokens_conversation_history,
    current_user_message: req.tokens_current_user_message,
    assistant_prefill: req.tokens_assistant_prefill,
    uncategorized: req.tokens_uncategorized,
  };
  const total = Object.values(values).reduce((a, b) => a + b, 0);

  if (total === 0) {
    return <div className="h-2 w-24 rounded-full bg-gray-800" />;
  }

  const segments = CATEGORY_ORDER
    .filter(cat => values[cat] > 0)
    .map(cat => ({
      key: cat,
      pct: (values[cat] / total) * 100,
      tooltip: `${CATEGORY_LABELS[cat]}: ${values[cat].toLocaleString()} (${((values[cat] / total) * 100).toFixed(1)}%)`,
    }));

  return (
    <div className="flex h-2 w-24 rounded-full overflow-hidden gap-px">
      {segments.map(seg => (
        <div
          key={seg.key}
          style={{ width: `${seg.pct}%`, backgroundColor: CATEGORY_COLORS[seg.key] }}
          title={seg.tooltip}
        />
      ))}
    </div>
  );
}

function statusBadge(code: number | null) {
  if (code === null) return null;
  let cls = 'bg-gray-700 text-gray-400';
  if (code >= 200 && code < 300) cls = 'bg-green-900 text-green-300';
  else if (code >= 400 && code < 500) cls = 'bg-orange-900 text-orange-300';
  else if (code >= 500) cls = 'bg-red-900 text-red-300';
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-mono font-medium ${cls}`}>
      {code}
    </span>
  );
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface Props {
  requests: Request[];
  sessions?: Session[];
  onRowClick: (id: string) => void;
}

export function RequestTable({ requests, sessions, onRowClick }: Props) {
  const [hideEmpty, setHideEmpty] = useState(true);
  const sessionMap = new Map((sessions ?? []).map(s => [s.id, s.name]));

  const visible = hideEmpty
    ? requests.filter(r => r.tokens_total_input > 0 || r.tokens_total_output > 0)
    : requests;

  if (requests.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500 text-sm">
        No requests captured yet.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      {/* Filter row */}
      <div className="flex items-center gap-2 mb-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideEmpty}
            onChange={e => setHideEmpty(e.target.checked)}
            className="accent-indigo-500 w-3.5 h-3.5 cursor-pointer"
          />
          Hide empty requests
        </label>
        {hideEmpty && requests.length !== visible.length && (
          <span className="text-xs text-gray-600">
            ({requests.length - visible.length} hidden)
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-700">
            <th className="pb-2 pr-4 font-medium">Time</th>
            <th className="pb-2 pr-4 font-medium">Session</th>
            <th className="pb-2 pr-4 font-medium">Provider</th>
            <th className="pb-2 pr-4 font-medium">Agent</th>
            <th className="pb-2 pr-4 font-medium">Model</th>
            <th className="pb-2 pr-4 font-medium">Context composition</th>
            <th className="pb-2 pr-4 font-medium text-right">Tokens (in)</th>
            <th className="pb-2 pr-4 font-medium text-right">Tokens (out)</th>
            <th className="pb-2 pr-4 font-medium text-right">Duration</th>
            <th className="pb-2 font-medium text-right">Status</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((req) => (
            <tr
              key={req.id}
              onClick={() => onRowClick(req.id)}
              className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer transition-colors"
            >
              <td className="py-2 pr-4 text-gray-400 font-mono text-xs">
                {formatTime(req.timestamp)}
              </td>
              <td className="py-2 pr-4">
                {req.session_id && sessionMap.has(req.session_id) ? (
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-900 text-indigo-300 truncate max-w-[100px] inline-block" title={sessionMap.get(req.session_id)}>
                    {sessionMap.get(req.session_id)}
                  </span>
                ) : (
                  <span className="text-gray-600 text-xs">n/a</span>
                )}
              </td>
              <td className="py-2 pr-4">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    PROVIDER_COLORS[req.provider] ?? PROVIDER_COLORS.unknown
                  }`}
                >
                  {req.provider}
                </span>
              </td>
              <td className="py-2 pr-4">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    AGENT_COLORS[req.agent ?? 'unknown'] ?? AGENT_COLORS.unknown
                  }`}
                >
                  {req.agent}
                </span>
              </td>
              <td className="py-2 pr-4 text-gray-300 truncate max-w-[140px]">
                {req.model ?? '—'}
              </td>
              <td className="py-2 pr-4">
                <ContextBar req={req} />
              </td>
              <td className="py-2 pr-4 text-right text-gray-300">
                {req.tokens_total_input > 0 ? req.tokens_total_input.toLocaleString() : '—'}
              </td>
              <td className="py-2 pr-4 text-right text-gray-300">
                {req.tokens_total_output > 0 ? req.tokens_total_output.toLocaleString() : '—'}
              </td>
              <td className="py-2 pr-4 text-right text-gray-400">
                {formatDuration(req.duration_ms)}
              </td>
              <td className="py-2 text-right">
                {statusBadge(req.status_code)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
