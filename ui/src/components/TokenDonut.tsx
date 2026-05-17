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
      color: CATEGORY_COLORS[key] ?? '#6b7280',
    }));

  if (entries.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No token data
      </div>
    );
  }

  const total = entries.reduce((sum, e) => sum + e.value, 0);

  return (
    <div className="flex items-center gap-4">
      {/* Donut — left 50% */}
      <div style={{ width: '50%', minWidth: 0 }}>
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={entries}
              cx="50%"
              cy="50%"
              innerRadius={60}
              outerRadius={90}
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
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '6px' }}
              labelStyle={{ color: '#f9fafb' }}
              itemStyle={{ color: '#d1d5db' }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Table — right 50% */}
      <div style={{ width: '50%', minWidth: 0 }} className="overflow-auto max-h-[220px]">
        <table className="w-full text-xs text-gray-300 border-separate border-spacing-0">
          <thead className="sticky top-0 bg-gray-800 z-10">
            <tr className="text-gray-500 uppercase tracking-wide">
              <th className="text-left pb-2 pr-3 font-medium border-b border-gray-700">Category</th>
              <th className="text-right pb-2 font-medium border-b border-gray-700 whitespace-nowrap">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.name} className="hover:bg-gray-700/30">
                <td className="py-1.5 pr-3 border-b border-gray-700/40">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: entry.color }} />
                    <span className="truncate" title={entry.name}>{entry.name}</span>
                  </div>
                </td>
                <td className="py-1.5 text-right tabular-nums border-b border-gray-700/40 whitespace-nowrap">
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

