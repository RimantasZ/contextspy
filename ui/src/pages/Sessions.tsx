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
import { formatElapsedDuration } from '../lib/format';

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
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`cursor-pointer select-none whitespace-nowrap px-4 py-3 font-medium hover:text-[var(--text)] ${className}`}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && <span className="text-[var(--accent-soft-text)]">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </span>
    </th>
  );
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
        aria-label="Session name"
        className="app-field w-48 py-1"
      />
      <button onClick={save} className="app-button h-8 w-8 px-0 text-[var(--success)]" title="Save" aria-label="Save session name">✓</button>
      <button onClick={onDone} className="app-button h-8 w-8 px-0" title="Cancel" aria-label="Cancel rename">✕</button>
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
  const [renderedAt] = useState(() => Date.now());

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

  // duration_ms is null only while a session is still active; approximate it
  // live from the start timestamp until the backend reports a final value.
  function getDurationMs(e: SessionSummaryEntry): number {
    if (e.duration_ms !== null) return e.duration_ms;
    return renderedAt - new Date(e.started_at).getTime();
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
    <div className="page-shell">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-[var(--text)]">Sessions</h1>
        <SessionControls />
      </div>

      <div className="surface overflow-x-auto rounded-lg border border-[var(--border)]">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-[var(--text-muted)]">Loading\u2026</div>
        ) : sessions.length === 0 ? (
          <div className="py-12 text-center text-sm text-[var(--text-muted)]">
            No sessions yet. Start one to group your requests.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="surface-inset border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
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
                  className="cursor-pointer border-b border-[var(--border)] transition-colors hover:bg-[var(--surface-hover)]"
                  onClick={() => navigate(`/sessions/${s.session_id}`)}
                >
                  <td className="px-4 py-3 font-medium text-[var(--text)]">
                    {editingId === s.session_id ? (
                      <InlineRename id={s.session_id} currentName={s.name ?? ''} onDone={() => setEditingId(null)} />
                    ) : (
                      s.name
                    )}
                  </td>
                  <td className="px-4 py-3 text-[var(--text-muted)]">
                    {new Date(s.started_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-[var(--text-muted)]">
                    {formatElapsedDuration(getDurationMs(s))}
                  </td>
                  <td className="px-4 py-3">
                    {s.ended_at === null ? (
                      <span className="app-badge status-success gap-1">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--success)]" />
                        Active
                      </span>
                    ) : (
                      <span className="app-badge">
                        Ended
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-[var(--text-muted)]">
                    {s.request_count}
                  </td>
                  <td className="px-4 py-3 text-right text-[var(--text)]">
                    {s.tokens_in > 0 ? s.tokens_in.toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3 text-right text-[var(--text)]">
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
                        className="app-button-ghost min-h-8 px-2 py-1 text-xs"
                      >
                        Rename
                      </button>
                      <button
                        onClick={() => setDeletingSession({ id: s.session_id, name: s.name ?? '' })}
                        className="app-button-danger-ghost min-h-8 px-2 py-1 text-xs"
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
