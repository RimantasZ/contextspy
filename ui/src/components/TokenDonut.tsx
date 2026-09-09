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
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { CATEGORY_COLORS } from './ContextBar';

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

interface Props {
  data: Record<string, number>;
}

export function TokenDonut({ data }: Props) {
  const entries = Object.entries(data)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([key, value]) => ({
      name: CATEGORY_LABELS[key] ?? key,
      value,
      color: CATEGORY_COLORS[key] ?? 'var(--category-other)',
    }));

  if (entries.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-[var(--text-muted)]">
        No token data
      </div>
    );
  }

  const total = entries.reduce((sum, e) => sum + e.value, 0);

  return (
    <div className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] items-center gap-4">
      <div className="min-w-0" role="img" aria-label={`Token composition. ${entries.map((entry) => `${entry.name}: ${entry.value.toLocaleString()}`).join('; ')}`}>
        <ResponsiveContainer width="100%" height={210}>
          <PieChart>
            <Pie
              data={entries}
              cx="50%"
              cy="50%"
              innerRadius={54}
              outerRadius={82}
              paddingAngle={2}
              dataKey="value"
            >
              {entries.map((entry, i) => (
                <Cell key={i} fill={entry.color} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value: number) => [
                `${value.toLocaleString()} tokens (${((value / total) * 100).toFixed(1)}%)`,
              ]}
              contentStyle={{ backgroundColor: 'var(--chart-tooltip)', border: '1px solid var(--border)', borderRadius: '6px' }}
              labelStyle={{ color: 'var(--chart-tooltip-text)' }}
              itemStyle={{ color: 'var(--text-muted)' }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      <div className="min-w-0 max-h-[220px] overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-xs text-[var(--text)]">
          <thead className="sticky top-0 z-10 bg-[var(--surface)]">
            <tr className="text-[var(--text-muted)]">
              <th className="border-b border-[var(--border)] pb-2 pr-3 text-left font-medium">Category</th>
              <th className="whitespace-nowrap border-b border-[var(--border)] pb-2 text-right font-medium">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.name} className="hover:bg-[var(--surface-muted)]">
                <td className="border-b border-[var(--border)] py-1.5 pr-3">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: entry.color }} />
                    <span className="truncate" title={entry.name}>{entry.name}</span>
                  </div>
                </td>
                <td className="whitespace-nowrap border-b border-[var(--border)] py-1.5 text-right tabular-nums">
                  {entry.value.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
