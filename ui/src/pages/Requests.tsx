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
import { useNavigate } from 'react-router-dom';
import { useRequests, useStatsOverview } from '../api/hooks';
import { RequestTable } from '../components/RequestTable';
import type { SortKey } from '../components/RequestTable';

const PAGE_SIZE = 50;

export default function Requests() {
  const navigate = useNavigate();
  const [provider, setProvider] = useState('');
  const [agent, setAgent] = useState('');
  const [q, setQ] = useState('');
  const [statusCategory, setStatusCategory] = useState('');
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  function handleSortChange(key: SortKey | null, dir: 'asc' | 'desc') {
    setSortKey(key);
    setSortDir(dir);
    setPage(0);
  }

  const stats = useStatsOverview();
  const modelOptions = Object.keys(stats.data?.by_model ?? {}).sort();

  const { data, isLoading } = useRequests({
    provider: provider || undefined,
    agent: agent || undefined,
    q: q || undefined,
    status_category: statusCategory || undefined,
    sort_by: sortKey ?? undefined,
    sort_dir: sortKey ? sortDir : undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const reqs = data?.requests ?? [];

  function resetPage() { setPage(0); }

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-white">All Requests</h1>

      {/* Filter bar */}
      <div className="flex gap-3 flex-wrap items-center">
        <input
          type="search"
          placeholder="Search model, endpoint, agent…"
          value={q}
          onChange={(e) => { setQ(e.target.value); resetPage(); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500 w-56"
        />
        <select
          value={provider}
          onChange={(e) => { setProvider(e.target.value); resetPage(); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500"
        >
          <option value="">All providers</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="ollama">Ollama</option>
        </select>
        <select
          value={agent}
          onChange={(e) => { setAgent(e.target.value); resetPage(); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500"
        >
          <option value="">All agents</option>
          <option value="copilot">Copilot</option>
          <option value="claude">Claude</option>
          <option value="cursor">Cursor</option>
          <option value="unknown">Unknown</option>
        </select>
        <select
          value={statusCategory}
          onChange={(e) => { setStatusCategory(e.target.value); resetPage(); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500"
        >
          <option value="">All statuses</option>
          <option value="success">Success (2xx)</option>
          <option value="error">Errors (4xx / 5xx)</option>
        </select>
        {(provider || agent || q || statusCategory) && (
          <button
            onClick={() => { setProvider(''); setAgent(''); setQ(''); setStatusCategory(''); resetPage(); }}
            className="text-sm text-gray-400 hover:text-white px-2"
          >
            Clear filters
          </button>
        )}
        {modelOptions.length > 0 && (
          <span className="text-xs text-gray-500 ml-auto">
            {modelOptions.length} model{modelOptions.length !== 1 ? 's' : ''} seen
          </span>
        )}
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-lg p-4">
        {isLoading ? (
          <div className="text-center py-12 text-gray-500 text-sm">Loading…</div>
        ) : (
          <RequestTable
            requests={reqs}
            onRowClick={(id) => navigate(`/requests/${id}`)}
            sortKey={sortKey}
            sortDir={sortDir}
            onSortChange={handleSortChange}
          />
        )}
      </div>

      {/* Pagination */}
      {(page > 0 || reqs.length === PAGE_SIZE) && (
        <div className="flex justify-between items-center">
          <button
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded disabled:opacity-40 hover:bg-gray-600"
          >
            Previous
          </button>
          <span className="text-sm text-gray-500">Page {page + 1}</span>
          <button
            disabled={reqs.length < PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}
            className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded disabled:opacity-40 hover:bg-gray-600"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
