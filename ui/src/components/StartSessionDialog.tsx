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
import { useEffect, useState } from 'react';
import { useCreateSession } from '../api/hooks';

export function StartSessionDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const createSession = useCreateSession();

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  function handleStart() {
    const trimmed = name.trim();
    if (!trimmed || createSession.isPending) return;
    createSession.mutate(trimmed, { onSuccess: onClose });
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-dialog w-80" role="dialog" aria-modal="true" aria-labelledby="start-session-title">
        <h2 id="start-session-title" className="mb-4 font-semibold">Start a session</h2>
        <input
          autoFocus
          type="text"
          placeholder="Session name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleStart()}
          aria-label="Session name"
          className="app-field mb-4 w-full"
        />
        <p className="mb-4 text-xs text-[var(--text-muted)]">Starting a session ends any session that is currently active.</p>
        {createSession.isError && (
          <p role="alert" className="notice-warning mb-4 text-xs">Could not start the session. Try again.</p>
        )}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="app-button-ghost">Cancel</button>
          <button
            onClick={handleStart}
            disabled={!name.trim() || createSession.isPending}
            className="app-button-primary"
          >
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
