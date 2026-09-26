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
import { useEffect, useMemo, useState } from 'react'
import { useDashboardLive } from '../../api/hooks'
import { ActiveSessionPanel } from './ActiveSessionPanel'
import { ConversationFlows } from './ConversationFlows'
import { ContextChangePanel } from './ContextChangePanel'
import { RequestActivityChart } from './RequestActivityChart'

export function LiveSessionSection() {
  const { data, isLoading, isError } = useDashboardLive()
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [selectionNotice, setSelectionNotice] = useState('')
  const groups = useMemo(() => data?.conversations ?? [], [data])
  const effectiveKey = selectedKey && groups.some((group) => group.key === selectedKey)
    ? selectedKey : data?.most_recent_conversation_key ?? groups[0]?.key ?? null
  const selectedGroup = groups.find((group) => group.key === effectiveKey)

  useEffect(() => {
    if (selectedKey && groups.length && !groups.some((group) => group.key === selectedKey)) {
      setSelectedKey(groups[0].key)
      setSelectionNotice('That conversation is no longer confirmed; showing the primary session sequence.')
    }
  }, [groups, selectedKey])

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
          {selectionNotice && <p role="status" className="text-xs text-[var(--text-muted)]">{selectionNotice}</p>}
          <ConversationFlows data={data} selectedKey={effectiveKey} onSelect={(key) => { setSelectedKey(key); setSelectionNotice('') }} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(240px,.4fr)]">
            <RequestActivityChart activity={data.activity} />
            <ContextChangePanel
              change={groups.length ? selectedGroup?.context_change ?? data.context_change : null}
              unavailable={!groups.length && data.request_flow.length > 0}
            />
          </div>
        </>
      )}
    </section>
  )
}
