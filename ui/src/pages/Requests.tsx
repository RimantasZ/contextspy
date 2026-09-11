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
import { useRequests, useSessions, useStatsOverview } from '../api/hooks';
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
  const sessions = useSessions();
  const providerOptions = Object.keys(stats.data?.by_provider ?? {}).sort((a, b) => a.localeCompare(b));
  const agentOptions = Object.keys(stats.data?.by_agent ?? {}).sort((a, b) => a.localeCompare(b));
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
    <div className="page-shell">
      <h1 className="text-2xl font-bold text-[var(--text)]">All Requests</h1>

      {/* Filter bar */}
      <div className="flex gap-3 flex-wrap items-center">
        <label className="min-w-[220px] flex-1 sm:max-w-xs">
          <span className="sr-only">Search requests</span>
          <input
            type="search"
            aria-label="Search requests"
            placeholder="Search model, endpoint, agent…"
            value={q}
            onChange={(e) => { setQ(e.target.value); resetPage(); }}
            className="app-field w-full py-1.5"
          />
        </label>
        <select
          aria-label="Filter by provider"
          value={provider}
          onChange={(e) => { setProvider(e.target.value); resetPage(); }}
          className="app-field py-1.5"
        >
          <option value="">All providers</option>
          {providerOptions.map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
        <select
          aria-label="Filter by agent"
          value={agent}
          onChange={(e) => { setAgent(e.target.value); resetPage(); }}
          className="app-field py-1.5"
        >
          <option value="">All agents</option>
          {agentOptions.map((value) => (
            <option key={value} value={value}>{value === 'unknown' ? 'Unknown' : value}</option>
          ))}
        </select>
        <select
          aria-label="Filter by status"
          value={statusCategory}
          onChange={(e) => { setStatusCategory(e.target.value); resetPage(); }}
          className="app-field py-1.5"
        >
          <option value="">All statuses</option>
          <option value="success">Success (2xx)</option>
          <option value="error">Errors (4xx / 5xx)</option>
        </select>
        {(provider || agent || q || statusCategory) && (
          <button
            onClick={() => { setProvider(''); setAgent(''); setQ(''); setStatusCategory(''); resetPage(); }}
            className="app-button-ghost"
          >
            Clear filters
          </button>
        )}
        {modelOptions.length > 0 && (
          <span className="ml-auto text-xs text-[var(--text-muted)]">
            {modelOptions.length} model{modelOptions.length !== 1 ? 's' : ''} seen
          </span>
        )}
      </div>

      {/* Table */}
      <div className="panel">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-[var(--text-muted)]">Loading…</div>
        ) : (
          <RequestTable
            requests={reqs}
            sessions={sessions.data?.sessions}
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
            className="app-button"
          >
            Previous
          </button>
          <span className="text-sm text-[var(--text-muted)]">Page {page + 1}</span>
          <button
            disabled={reqs.length < PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}
            className="app-button"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
