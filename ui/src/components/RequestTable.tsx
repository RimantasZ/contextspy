import type { Request } from '../api/client';

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
  onRowClick: (id: string) => void;
}

export function RequestTable({ requests, onRowClick }: Props) {
  if (requests.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500 text-sm">
        No requests captured yet.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-700">
            <th className="pb-2 pr-4 font-medium">Time</th>
            <th className="pb-2 pr-4 font-medium">Provider</th>
            <th className="pb-2 pr-4 font-medium">Agent</th>
            <th className="pb-2 pr-4 font-medium">Model</th>
            <th className="pb-2 pr-4 font-medium text-right">Tokens (in)</th>
            <th className="pb-2 pr-4 font-medium text-right">Tokens (out)</th>
            <th className="pb-2 font-medium text-right">Duration</th>
          </tr>
        </thead>
        <tbody>
          {requests.map((req) => (
            <tr
              key={req.id}
              onClick={() => onRowClick(req.id)}
              className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer transition-colors"
            >
              <td className="py-2 pr-4 text-gray-400 font-mono text-xs">
                {formatTime(req.timestamp)}
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
              <td className="py-2 pr-4 text-right text-gray-300">
                {req.tokens_total_input > 0 ? req.tokens_total_input.toLocaleString() : '—'}
              </td>
              <td className="py-2 pr-4 text-right text-gray-300">
                {req.tokens_total_output > 0 ? req.tokens_total_output.toLocaleString() : '—'}
              </td>
              <td className="py-2 text-right text-gray-400">
                {formatDuration(req.duration_ms)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
