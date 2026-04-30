import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRequests } from '../api/hooks';
import { RequestTable } from '../components/RequestTable';

const PAGE_SIZE = 50;

export default function Requests() {
  const navigate = useNavigate();
  const [provider, setProvider] = useState('');
  const [agent, setAgent] = useState('');
  const [page, setPage] = useState(0);

  const { data, isLoading } = useRequests({
    provider: provider || undefined,
    agent: agent || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const reqs = data?.requests ?? [];

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold text-white">Requests</h1>

      {/* Filter bar */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={provider}
          onChange={(e) => { setProvider(e.target.value); setPage(0); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500"
        >
          <option value="">All providers</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="ollama">Ollama</option>
        </select>
        <select
          value={agent}
          onChange={(e) => { setAgent(e.target.value); setPage(0); }}
          className="bg-gray-700 text-gray-300 text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-indigo-500"
        >
          <option value="">All agents</option>
          <option value="copilot">Copilot</option>
          <option value="claude">Claude</option>
          <option value="cursor">Cursor</option>
          <option value="unknown">Unknown</option>
        </select>
        {(provider || agent) && (
          <button
            onClick={() => { setProvider(''); setAgent(''); setPage(0); }}
            className="text-sm text-gray-400 hover:text-white px-2"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-lg p-4">
        {isLoading ? (
          <div className="text-center py-12 text-gray-500 text-sm">Loading\u2026</div>
        ) : (
          <RequestTable requests={reqs} onRowClick={(id) => navigate(`/requests/${id}`)} />
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
