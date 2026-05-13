import { useParams, useNavigate } from 'react-router-dom';
import { useRequest, useRequestToolStats } from '../api/hooks';
import { TokenDonut } from '../components/TokenDonut';
import { RawViewer } from '../components/RawViewer';
import { ToolBreakdownCharts, ToolBreakdownTable } from '../components/ToolBreakdown';

const CATEGORY_LABELS: Record<string, string> = {
  system_prompt: 'System Prompt',
  tool_definitions: 'Tool Definitions',
  tool_results: 'Tool Results',
  file_contents: 'File Contents',
  conversation_history: 'Conversation History',
  current_user_message: 'Current User Message',
  assistant_prefill: 'Assistant Prefill',
  uncategorized: 'Uncategorized',
};

function categoryDataFromRequest(req: {
  tokens_system_prompt: number;
  tokens_tool_definitions: number;
  tokens_tool_results: number;
  tokens_file_contents: number;
  tokens_conversation_history: number;
  tokens_current_user_message: number;
  tokens_assistant_prefill: number;
  tokens_uncategorized: number;
}): Record<string, number> {
  return {
    system_prompt: req.tokens_system_prompt,
    tool_definitions: req.tokens_tool_definitions,
    tool_results: req.tokens_tool_results,
    file_contents: req.tokens_file_contents,
    conversation_history: req.tokens_conversation_history,
    current_user_message: req.tokens_current_user_message,
    assistant_prefill: req.tokens_assistant_prefill,
    uncategorized: req.tokens_uncategorized,
  };
}

export default function RequestDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error } = useRequest(id ?? '');
  const toolStats = useRequestToolStats(id ?? '');

  if (isLoading) {
    return <div className="p-6 text-gray-400">Loading\u2026</div>;
  }
  if (error || !data) {
    return <div className="p-6 text-red-400">Request not found.</div>;
  }

  const req = data.request;
  const catData = categoryDataFromRequest(req);
  const total = req.tokens_total_input;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(-1)}
          className="text-gray-400 hover:text-white text-sm"
        >
          ← Back
        </button>
        <h1 className="text-xl font-bold text-white">Request detail</h1>
      </div>

      {/* Metadata */}
      <div className="bg-gray-800 rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        {[
          ['Provider', req.provider],
          ['Agent', req.agent ?? '—'],
          ['Model', req.model ?? '—'],
          ['Status', req.status_code ?? '—'],
          ['Time', new Date(req.timestamp).toLocaleString()],
          ['Duration', req.duration_ms != null ? `${req.duration_ms}ms` : '—'],
          ['Tokens in', req.tokens_total_input.toLocaleString()],
          ['Tokens out', req.tokens_total_output.toLocaleString()],
        ].map(([k, v]) => (
          <div key={String(k)}>
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">{k}</p>
            <p className="text-white font-medium">{v}</p>
          </div>
        ))}
      </div>

      {/* Charts + breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Token composition</p>
          <TokenDonut data={catData} />
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-sm font-medium text-gray-300 mb-3">Category breakdown</p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400 border-b border-gray-700">
                <th className="pb-2 font-medium">Category</th>
                <th className="pb-2 font-medium text-right">Tokens</th>
                <th className="pb-2 font-medium text-right">%</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(catData)
                .filter(([, v]) => (v as number) > 0)
                .sort(([, a], [, b]) => (b as number) - (a as number))
                .map(([key, val]) => (
                  <tr key={key} className="border-b border-gray-800">
                    <td className="py-1.5 text-gray-300">{CATEGORY_LABELS[key] ?? key}</td>
                    <td className="py-1.5 text-right text-gray-300">{(val as number).toLocaleString()}</td>
                    <td className="py-1.5 text-right text-gray-400">
                      {total > 0 ? `${(((val as number) / total) * 100).toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          {(req.provider_input_tokens != null || req.provider_output_tokens != null) && (
            <div className="mt-4 pt-4 border-t border-gray-700 text-xs text-gray-400">
              <p className="font-medium text-gray-300 mb-1">Provider-reported vs estimated</p>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span>Input: </span>
                  <span className="text-white">{req.provider_input_tokens?.toLocaleString() ?? '—'}</span>
                  <span className="ml-1">(est. {req.tokens_total_input.toLocaleString()})</span>
                  {(req.cache_read_tokens != null || req.cache_creation_tokens != null) && (
                    <div className="mt-0.5 pl-0 space-y-0.5">
                      {(req.cache_read_tokens ?? 0) > 0 && (
                        <div className="text-teal-400">
                          ↳ {req.cache_read_tokens!.toLocaleString()} read from cache
                        </div>
                      )}
                      {(req.cache_creation_tokens ?? 0) > 0 && (
                        <div className="text-amber-400">
                          ↳ {req.cache_creation_tokens!.toLocaleString()} written to cache
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div>
                  <span>Output: </span>
                  <span className="text-white">{req.provider_output_tokens?.toLocaleString() ?? '—'}</span>
                  <span className="ml-1">(est. {req.tokens_total_output.toLocaleString()})</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Tool breakdown */}
      {(toolStats.data?.tools ?? []).length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <ToolBreakdownCharts tools={toolStats.data!.tools} />
          <ToolBreakdownTable tools={toolStats.data!.tools} totalInputTokens={req.tokens_total_input} />
        </div>
      )}

      {/* Raw bodies */}
      <div className="space-y-3">
        <RawViewer title="Request" content={req.raw_request_body} parsedBody={req.raw_request_body} totalInputTokens={req.tokens_total_input} />
        <RawViewer title="Response" content={req.raw_response_body} responseMode />
      </div>
    </div>
  );
}
