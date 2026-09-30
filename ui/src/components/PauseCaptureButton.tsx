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
import { useProxyStatus, useSetCapturePaused } from '../api/hooks';

export function PauseCaptureButton() {
  const { data: status } = useProxyStatus();
  const setPaused = useSetCapturePaused();

  const paused = status?.paused ?? false;

  return (
    <button
      type="button"
      onClick={() => setPaused.mutate(!paused)}
      disabled={!status || setPaused.isPending}
      aria-pressed={paused}
      aria-label={paused ? 'Resume capture' : 'Pause capture'}
      title={paused ? 'Capture is paused — requests are being ignored. Click to resume.' : 'Ignore requests until resumed'}
      className={`${paused ? 'app-button-primary' : 'app-button'} min-h-9 w-full justify-start gap-2`}
    >
      <span aria-hidden="true" className="w-5 text-center">{paused ? '▶' : '⏸'}</span>
      <span className="nav-label whitespace-nowrap">{paused ? 'Resume capture' : 'Pause capture'}</span>
    </button>
  );
}
