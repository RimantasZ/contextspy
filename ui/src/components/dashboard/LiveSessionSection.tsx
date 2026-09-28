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
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useDashboardLive, useRequestContext } from '../../api/hooks'
import { ActiveSessionPanel } from './ActiveSessionPanel'
import { ConversationFlows } from './ConversationFlows'
import { ContextChangePanel } from './ContextChangePanel'
import { RequestActivityChart } from './RequestActivityChart'
import { RequestFlow } from './RequestFlow'
import { RequestViewControls } from './RequestViewControls'
import { useCompactRequestCards, useSelectedRequest } from './requestViewState'

export function LiveSessionSection() {
  const { data, isLoading, isError } = useDashboardLive()
  const [layout, setLayout] = useState<'sequence' | 'conversations'>('sequence')
  const [selectionNotice, setSelectionNotice] = useState('')
  const [compact, setCompact] = useCompactRequestCards()
  const newestId = data?.request_flow[0]?.id ?? null
  const [selectedId, setSelectedId] = useSelectedRequest(newestId)
  const context = useRequestContext(data?.active_session?.id ?? '',
    selectedId && selectedId !== newestId ? selectedId : null, data?.sequence_revision)

  useEffect(() => {
    if (selectedId && selectedId !== newestId && newestId &&
        context.error instanceof Error && context.error.message.includes('API error 404')) {
      setSelectedId(newestId)
      setSelectionNotice('Selected request is no longer in this session; showing the newest request.')
    }
  }, [context.error, newestId, selectedId, setSelectedId])

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
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,.6fr)]">
            <RequestActivityChart activity={data.activity} />
            <ContextChangePanel
              change={selectedId === newestId ? data.context_change : context.data?.context_change ?? null}
              pending={!!selectedId && selectedId !== newestId && context.isLoading}
              failed={!!selectedId && selectedId !== newestId && context.isError}
            />
          </div>
          <RequestViewControls compact={compact} onCompactChange={setCompact} layout={layout} onLayoutChange={setLayout}
            groupCount={data.conversation_count + (data.auxiliary ? 1 : 0)} />
          {layout === 'sequence' ? <RequestFlow items={data.request_flow} compact={compact} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setSelectionNotice('') }}
            trailingAction={data.sequence_next_cursor && <Link className="app-button h-full w-full text-center" to={`/sessions/${data.active_session.id}?view=lineage`}>More ({data.active_session.request_count} total) →</Link>} />
            : <ConversationFlows data={data} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setSelectionNotice('') }} compact={compact} />}
        </>
      )}
    </section>
  )
}
