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
import { useState, useEffect, useRef } from 'react';
import { useDeleteSession } from '../api/hooks';

interface Props {
  sessionId: string;
  sessionName: string;
  onClose: () => void;
  onDeleted?: () => void;
}

export function DeleteSessionModal({ sessionId, sessionName, onClose, onDeleted }: Props) {
  const [deleteRequests, setDeleteRequests] = useState(false);
  const deleteSession = useDeleteSession();
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus cancel on open
  useEffect(() => { cancelRef.current?.focus(); }, []);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  function handleDelete() {
    deleteSession.mutate(
      { id: sessionId, deleteRequests },
      { onSuccess: () => { onClose(); onDeleted?.(); } },
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-sm mx-4 p-6 space-y-4">
        <h2 className="text-white font-semibold text-base">
          Delete session &ldquo;{sessionName}&rdquo;
        </h2>

        <p className="text-gray-400 text-sm">
          This will permanently remove the session record. Requests can be kept or deleted.
        </p>

        <label className="flex items-center gap-2.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={deleteRequests}
            onChange={(e) => setDeleteRequests(e.target.checked)}
            className="w-4 h-4 rounded border-gray-500 bg-gray-700 text-red-500 focus:ring-red-500 focus:ring-offset-gray-800"
          />
          <span className="text-sm text-gray-300">
            Also delete all requests in this session
          </span>
        </label>

        <div className="flex justify-end gap-2 pt-2">
          <button
            ref={cancelRef}
            onClick={onClose}
            className="px-4 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded"
          >
            Cancel
          </button>
          <button
            onClick={handleDelete}
            disabled={deleteSession.isPending}
            className="px-4 py-1.5 text-sm bg-red-700 hover:bg-red-600 text-white rounded disabled:opacity-50"
          >
            {deleteSession.isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}
