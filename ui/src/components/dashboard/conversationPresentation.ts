import type { DashboardConversation } from '../../api/client'

/** Display wording only; the backend owns conversation evidence and membership. */
export function evidenceLabel(group: Pick<DashboardConversation, 'evidence' | 'fork_parent_request_id'>): string {
  if (group.evidence === 'fork') return `Confirmed fork from ${group.fork_parent_request_id?.slice(0, 8) ?? 'request'}`
  if (group.evidence === 'parallel_chains') return 'Confirmed parallel independent chains'
  if (group.evidence === 'stream_affinity') return 'Supported separate stream · corroborated by context'
  if (group.evidence === 'independent_agent_stream') return 'Separate agent stream · sustained chain and distinct context'
  if (group.evidence === 'distinct_stream_hint') return 'Distinct stream hint · sustained chain and distinct context'
  if (group.evidence === 'sustained_chain') return 'Sustained chain · continuity to other streams not established'
  if (group.evidence === 'unresolved_stream') return 'Sustained stream · relationship to other conversations not established'
  if (group.evidence === 'auxiliary') return 'Unclassified or one-off requests; these may move into a conversation as more evidence arrives.'
  return 'Session activity; separate streams not confirmed'
}

export function gapLabel(reason: string | null): string | null {
  if (reason === 'fork_branch') return 'Fork branch · parent shown in shared history'
  if (reason === 'graph_branch_unconfirmed') return 'Graph branch · separate conversation not confirmed'
  if (reason === 'ambiguous') return 'Lineage gap · ambiguous parent'
  if (reason === 'unresolved_exact') return 'Lineage gap · provider parent missing'
  if (reason === 'unavailable') return 'Lineage gap · context unavailable'
  if (reason === 'root') return 'Lineage gap · no parent established'
  if (reason === 'external') return 'Continues from another session'
  if (reason === 'stream_resume_unlinked') return 'Same stream · direct predecessor not established'
  return null
}
