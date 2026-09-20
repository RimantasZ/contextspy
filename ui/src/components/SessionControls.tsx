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
import { useSessions, useEndSession } from '../api/hooks';
import { StartSessionDialog } from './StartSessionDialog';

export function SessionControls() {
  const [showModal, setShowModal] = useState(false);

  const { data: sessions } = useSessions();
  const endSession = useEndSession();

  const active = sessions?.sessions?.find((s) => s.ended_at === null);

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
              onClick={() => endSession.mutate(active.id)}
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

      {showModal && <StartSessionDialog onClose={() => setShowModal(false)} />}
    </>
  );
}
