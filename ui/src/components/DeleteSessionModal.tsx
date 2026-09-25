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
      className="modal-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="modal-dialog mx-4 w-full max-w-sm space-y-4" role="dialog" aria-modal="true" aria-labelledby="delete-session-title">
        <h2 id="delete-session-title" className="text-base font-semibold">
          Delete session &ldquo;{sessionName}&rdquo;
        </h2>

        <p className="text-sm text-[var(--text-muted)]">
          This will permanently remove the session record. Requests can be kept or deleted.
        </p>

        <label className="flex items-center gap-2.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={deleteRequests}
            onChange={(e) => setDeleteRequests(e.target.checked)}
            className="h-4 w-4 rounded"
          />
          <span className="text-sm text-[var(--text)]">
            Also delete all requests in this session
          </span>
        </label>

        <div className="flex justify-end gap-2 pt-2">
          <button
            ref={cancelRef}
            onClick={onClose}
            className="app-button"
          >
            Cancel
          </button>
          <button
            onClick={handleDelete}
            disabled={deleteSession.isPending}
            className="app-button-danger"
          >
            {deleteSession.isPending ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}
