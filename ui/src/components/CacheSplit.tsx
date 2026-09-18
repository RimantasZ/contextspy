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
import type { CacheStats } from '../api/client';

/**
 * Inline "N% avg cached · N% overall" split of provider-reported prompt-cache
 * usage across a session, mirroring OutputSplit's styling. Two numbers because
 * they answer different questions: avg_pct weights every request equally,
 * overall_pct is token-weighted across the session. Renders nothing when no
 * request in the session reported cache usage at all.
 */
export function CacheSplit({ cache }: { cache: CacheStats }) {
  if (cache.avg_pct == null || cache.overall_pct == null) return null;
  return (
    <>
      {cache.avg_pct.toFixed(1)}% avg cached · {cache.overall_pct.toFixed(1)}% overall
    </>
  );
}
