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
import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionsSummary, useRenameSession } from '../api/hooks';
import { SessionControls } from '../components/SessionControls';
import { DeleteSessionModal } from '../components/DeleteSessionModal';
import { ContextBar } from '../components/ContextBar';
import type { SessionSummaryEntry } from '../api/client';

type SessionSortKey = 'name' | 'started_at' | 'duration' | 'status' | 'request_count' | 'tokens_in' | 'tokens_out';

function SortHeader({
  label, col, sortKey, sortDir, onSort, className = '',
}: {
  label: string; col: SessionSortKey;
  sortKey: SessionSortKey | null; sortDir: 'asc' | 'desc';
  onSort: (col: SessionSortKey) => void; className?: string;
}) {
  const active = sortKey === col;
  return (
    <th
      className={`px-4 py-3 font-medium cursor-pointer select-none whitespace-nowrap hover:text-gray-200 ${className}`}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && <span className="text-indigo-400">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </span>
    </th>
  );
}

function formatDuration(start: string, end: string | null): string {
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((to - from) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

function InlineRename({ id, currentName, onDone }: { id: string; currentName: string; onDone: () => void }) {
  const [value, setValue] = useState(currentName);
  const inputRef = useRef<HTMLInputElement>(null);
  const rename = useRenameSession();

  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);

  function save() {
    const trimmed = value.trim();
    if (trimmed && trimmed !== currentName) {
      rename.mutate({ id, name: trimmed }, { onSuccess: onDone, onError: onDone });
    } else {
      onDone();
    }
  }

  return (
    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onDone(); }}
        className="bg-gray-700 text-white text-sm rounded px-2 py-0.5 border border-gray-500 focus:outline-none focus:border-indigo-400 w-48"
      />
      <button onClick={save} className="text-green-400 hover:text-green-300 text-xs px-1" title="Save">✓</button>
      <button onClick={onDone} className="text-gray-400 hover:text-gray-300 text-xs px-1" title="Cancel">✕</button>
    </div>
  );
}

export default function Sessions() {
  const navigate = useNavigate();
  const { data, isLoading } = useSessionsSummary();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingSession, setDeletingSession] = useState<{ id: string; name: string } | null>(null);
  const [sortKey, setSortKey] = useState<SessionSortKey | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const sessions = (data?.entries ?? []).filter(
    (e): e is SessionSummaryEntry & { session_id: string } => e.type === 'session' && e.session_id !== null
  );

  function handleSort(col: SessionSortKey) {
    if (sortKey === col) {
      if (sortDir === 'asc') setSortDir('desc');
      else { setSortKey(null); setSortDir('asc'); }
    } else {
      setSortKey(col);
      setSortDir('asc');
    }
  }

  function getDurationMs(e: SessionSummaryEntry): number {
    const from = new Date(e.started_at).getTime();
    const to = e.ended_at ? new Date(e.ended_at).getTime() : Date.now();
    return to - from;
  }

  const sorted = sortKey
    ? [...sessions].sort((a, b) => {
        let av: number | string;
        let bv: number | string;
        switch (sortKey) {
          case 'name': av = a.name ?? ''; bv = b.name ?? ''; break;
          case 'started_at': av = a.started_at; bv = b.started_at; break;
          case 'duration': av = getDurationMs(a); bv = getDurationMs(b); break;
          case 'status': av = a.is_active ? 1 : 0; bv = b.is_active ? 1 : 0; break;
          case 'request_count': av = a.request_count; bv = b.request_count; break;
          case 'tokens_in': av = a.tokens_in; bv = b.tokens_in; break;
          case 'tokens_out': av = a.tokens_out; bv = b.tokens_out; break;
          default: return 0;
        }
        if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
        return sortDir === 'asc' ? av - (bv as number) : (bv as number) - av;
      })
    : sessions;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Sessions</h1>
        <SessionControls />
      </div>

      <div className="bg-gray-800 rounded-lg overflow-hidden">
        {isLoading ? (
          <div className="text-center py-12 text-gray-500 text-sm">Loading\u2026</div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-12 text-gray-500 text-sm">
            No sessions yet. Start one to group your requests.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 uppercase tracking-wide border-b border-gray-700 bg-gray-900">
                <SortHeader label="Name" col="name" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Started" col="started_at" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Duration" col="duration" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Status" col="status" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortHeader label="Reqs" col="request_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
                <SortHeader label="Tokens in" col="tokens_in" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
                <SortHeader label="Tokens out" col="tokens_out" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="text-right" />
                <th className="px-4 py-3 font-medium whitespace-nowrap" style={{ minWidth: 256 }}>Context</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((s) => (
                <tr
                  key={s.session_id}
                  className="border-b border-gray-700 hover:bg-gray-750 cursor-pointer transition-colors"
                  onClick={() => navigate(`/sessions/${s.session_id}`)}
                >
                  <td className="px-4 py-3 text-white font-medium">
                    {editingId === s.session_id ? (
                      <InlineRename id={s.session_id} currentName={s.name ?? ''} onDone={() => setEditingId(null)} />
                    ) : (
                      s.name
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {new Date(s.started_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {formatDuration(s.started_at, s.ended_at)}
                  </td>
                  <td className="px-4 py-3">
                    {s.ended_at === null ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-900 text-green-300 text-xs">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                        Active
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full bg-gray-700 text-gray-400 text-xs">
                        Ended
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-400">
                    {s.request_count}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-300">
                    {s.tokens_in > 0 ? s.tokens_in.toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-300">
                    {s.tokens_out > 0 ? s.tokens_out.toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <ContextBar data={s} />
                  </td>
                  <td
                    className="px-4 py-3 text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="flex items-center justify-end gap-3">
                      <button
                        onClick={() => setEditingId(s.session_id)}
                        className="text-xs text-gray-400 hover:text-white"
                      >
                        Rename
                      </button>
                      <button
                        onClick={() => setDeletingSession({ id: s.session_id, name: s.name ?? '' })}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {deletingSession && (
        <DeleteSessionModal
          sessionId={deletingSession.id}
          sessionName={deletingSession.name}
          onClose={() => setDeletingSession(null)}
        />
      )}
    </div>
  );
}
