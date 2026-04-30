import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from 'recharts';

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
    <ResponsiveContainer width="100%" height={240}>
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
        <Legend
          formatter={(value) => <span style={{ color: '#d1d5db', fontSize: '12px' }}>{value}</span>}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
