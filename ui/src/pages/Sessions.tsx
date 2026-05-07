import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessions, useDeleteSession, useRenameSession } from '../api/hooks';
import { SessionControls } from '../components/SessionControls';

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
  const { data, isLoading } = useSessions();
  const deleteSession = useDeleteSession();
  const [editingId, setEditingId] = useState<string | null>(null);

  const sessions = data?.sessions ?? [];

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
              <tr className="text-left text-gray-400 border-b border-gray-700 bg-gray-900">
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Started</th>
                <th className="px-4 py-3 font-medium">Duration</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr
                  key={s.id}
                  className="border-b border-gray-700 hover:bg-gray-750 cursor-pointer transition-colors"
                  onClick={() => navigate(`/sessions/${s.id}`)}
                >
                  <td className="px-4 py-3 text-white font-medium">
                    {editingId === s.id ? (
                      <InlineRename id={s.id} currentName={s.name} onDone={() => setEditingId(null)} />
                    ) : (
                      <div className="flex items-center gap-2 group">
                        <span>{s.name}</span>
                        <button
                          onClick={(e) => { e.stopPropagation(); setEditingId(s.id); }}
                          className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-white transition-opacity"
                          title="Rename session"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536M9 11l6.536-6.536a2 2 0 012.828 0l.172.172a2 2 0 010 2.828L12 14H9v-3z" />
                          </svg>
                        </button>
                      </div>
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
                  <td
                    className="px-4 py-3 text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={() => {
                        if (confirm(`Delete session "${s.name}"?`)) {
                          deleteSession.mutate(s.id);
                        }
                      }}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
