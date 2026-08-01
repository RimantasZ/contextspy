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
import type { ReactNode } from 'react';

/**
 * Inline "N output · N thinking" split of generated tokens, shared by the
 * request detail, session detail and overview surfaces so the wording and
 * colour stay in step. Renders `fallback` when the model did no reasoning
 * (or the provider disclosed none), rather than a noisy "· 0 thinking".
 */
export function OutputSplit({
  text,
  thinking,
  fallback = null,
}: {
  text: number;
  thinking: number;
  fallback?: ReactNode;
}) {
  if (thinking <= 0) return <>{fallback}</>;
  return (
    <>
      {text.toLocaleString()} output ·{' '}
      <span className="text-violet-400">{thinking.toLocaleString()} thinking</span>
    </>
  );
}
