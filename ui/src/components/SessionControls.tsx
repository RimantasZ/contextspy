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
            <span className="text-sm text-green-400">
              <span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-1 animate-pulse" />
              {active.name}
            </span>
            <button
              onClick={handleEnd}
              disabled={endSession.isPending}
              className="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 text-white rounded disabled:opacity-50"
            >
              End session
            </button>
          </>
        ) : (
          <>
            <span className="text-sm text-gray-500">No active session</span>
            <button
              onClick={() => setShowModal(true)}
              className="px-3 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded"
            >
              Start session
            </button>
          </>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-800 rounded-lg p-6 w-80 shadow-xl">
            <h2 className="text-white font-semibold mb-4">Start a session</h2>
            <input
              autoFocus
              type="text"
              placeholder="Session name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleStart()}
              className="w-full px-3 py-2 bg-gray-700 text-white border border-gray-600 rounded text-sm focus:outline-none focus:border-indigo-500 mb-4"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowModal(false)}
                className="px-3 py-1 text-sm text-gray-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={handleStart}
                disabled={!name.trim() || createSession.isPending}
                className="px-4 py-1 text-sm bg-indigo-600 hover:bg-indigo-500 text-white rounded disabled:opacity-50"
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
