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
import React from 'react';
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

  function pctDiff(reported: number, estimated: number): string {
    if (estimated === 0) return '';
    const diff = ((reported - estimated) / estimated) * 100;
    const sign = diff >= 0 ? '+' : '';
    return ` (${sign}${diff.toFixed(1)}%)`;
  }

  const cacheHasData = req.cache_read_tokens != null || req.cache_creation_tokens != null;
  const cacheReadVal = req.cache_read_tokens ?? 0;
  const cacheWriteVal = req.cache_creation_tokens ?? 0;

  const metaFields: Array<{ label: string; value: React.ReactNode }> = [
    { label: 'Provider', value: req.provider },
    { label: 'Agent', value: req.agent ?? '—' },
    { label: 'Model', value: req.model ?? '—' },
    { label: 'Status', value: req.status_code ?? '—' },
    { label: 'Time', value: new Date(req.timestamp).toLocaleString() },
    { label: 'Duration', value: req.duration_ms != null ? `${req.duration_ms}ms` : '—' },
    {
      label: 'Cache',
      value: !cacheHasData ? (
        <span className="text-gray-500">N/A</span>
      ) : cacheReadVal === 0 && cacheWriteVal === 0 ? (
        <span className="text-gray-500">none</span>
      ) : (
        <span className="space-x-2">
          {cacheReadVal > 0 && (
            <span className="text-teal-400">↓ {cacheReadVal.toLocaleString()} read</span>
          )}
          {cacheWriteVal > 0 && (
            <span className="text-amber-400">↑ {cacheWriteVal.toLocaleString()} write</span>
          )}
        </span>
      ),
    },
    {
      label: 'API input tokens',
      value: req.provider_input_tokens != null ? (
        <span>
          {req.provider_input_tokens.toLocaleString()}
          <span className="text-gray-400 text-xs ml-1">
            {pctDiff(req.provider_input_tokens, req.tokens_total_input)}
          </span>
        </span>
      ) : (
        <span className="text-gray-500">N/A</span>
      ),
    },
    {
      label: 'API output tokens',
      value: req.provider_output_tokens != null ? (
        <span>
          {req.provider_output_tokens.toLocaleString()}
          <span className="text-gray-400 text-xs ml-1">
            {pctDiff(req.provider_output_tokens, req.tokens_total_output)}
          </span>
        </span>
      ) : (
        <span className="text-gray-500">N/A</span>
      ),
    },
  ];

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

      {/* Metadata: token stat panels left | fields right */}
      <div className="flex gap-4">
        {/* Left: stacked stat panels (~25%) */}
        <div className="flex flex-col gap-4 w-1/4 shrink-0">
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Context tokens</p>
            <p className="text-2xl font-semibold text-white">{req.tokens_total_input.toLocaleString()}</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Generated tokens</p>
            <p className="text-2xl font-semibold text-white">{req.tokens_total_output.toLocaleString()}</p>
          </div>
        </div>
        {/* Right: metadata grid (~75%) */}
        <div className="flex-1 bg-gray-800 rounded-lg p-4 grid grid-cols-3 gap-x-6 gap-y-4 text-sm">
          {metaFields.map(({ label, value }) => (
            <div key={label}>
              <p className="text-xs text-gray-400 uppercase tracking-wide mb-0.5">{label}</p>
              <p className="text-white font-medium">{value}</p>
            </div>
          ))}
        </div>
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
        <RawViewer title="Response" content={req.raw_response_body} responseMode totalInputTokens={req.tokens_total_output} />
      </div>
    </div>
  );
}
