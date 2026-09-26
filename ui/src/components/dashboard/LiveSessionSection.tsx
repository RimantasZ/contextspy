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
import { useDashboardLive } from '../../api/hooks'
import { ActiveSessionPanel } from './ActiveSessionPanel'
import { ContextChangePanel } from './ContextChangePanel'
import { RequestActivityChart } from './RequestActivityChart'
import { RequestFlow } from './RequestFlow'

export function LiveSessionSection() {
  const { data, isLoading, isError } = useDashboardLive()

  if (isLoading) {
    return (
      <section aria-label="Active session" aria-busy="true" className="space-y-4">
        <div className="panel min-h-[5.5rem] text-sm text-[var(--text-muted)]">Loading live session…</div>
        <div className="panel min-h-[8rem]" />
      </section>
    )
  }

  if (isError || !data) {
    return (
      <section aria-label="Active session">
        <p role="alert" className="notice-warning">Live session data could not be loaded.</p>
      </section>
    )
  }

  return (
    <section aria-label="Active session" className="space-y-4">
      <ActiveSessionPanel session={data.active_session} />
      {data.active_session && (
        <>
          <RequestFlow items={data.request_flow} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(240px,.4fr)]">
            <RequestActivityChart activity={data.activity} />
            <ContextChangePanel change={data.context_change} />
          </div>
        </>
      )}
    </section>
  )
}
