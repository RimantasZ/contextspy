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
import { useSessions, useCreateSession, useEndSession } from '../api/hooks';

export function SessionControls() {
  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState('');

  const { data: sessions } = useSessions();
  const createSession = useCreateSession();
  const endSession = useEndSession();

  const active = sessions?.sessions?.find((s) => s.ended_at === null);

  function handleStart() {
    if (!name.trim()) return;
    createSession.mutate(
      name.trim(),
      {
        onSuccess: () => {
          setName('');
          setShowModal(false);
        },
      }
    );
  }

  function handleEnd() {
    if (!active) return;
    endSession.mutate(active.id);
  }

  return (
    <>
      <div className="flex items-center gap-2">
        {active ? (
          <>
            <span className="text-sm text-[var(--success)]">
              <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--success)]" />
              {active.name}
            </span>
            <button
              onClick={handleEnd}
              disabled={endSession.isPending}
              className="app-button-danger min-h-8 py-1 text-xs"
            >
              End session
            </button>
          </>
        ) : (
          <>
            <span className="text-sm text-[var(--text-muted)]">No active session</span>
            <button
              onClick={() => setShowModal(true)}
              className="app-button-primary min-h-8 py-1 text-xs"
            >
              Start session
            </button>
          </>
        )}
      </div>

      {showModal && (
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
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowModal(false)}
                className="app-button-ghost"
              >
                Cancel
              </button>
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
      )}
    </>
  );
}
