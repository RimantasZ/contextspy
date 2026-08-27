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
// API base URL — works both in dev (proxied by Vite) and prod (same origin)
const BASE = '/api'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`API error ${res.status}: ${err}`)
  }
  return res.json() as Promise<T>
}

// ---- Types ----------------------------------------------------------------

export interface Session {
  id: string
  name: string
  started_at: string
  ended_at: string | null
  is_active: boolean
}

export interface Request {
  id: string
  session_id: string | null
  logical_request_id: string | null
  timestamp: string
  provider: string
  model: string | null
  agent: string | null
  endpoint: string
  duration_ms: number | null
  ttft_ms: number | null
  status_code: number | null
  transport: string
  response_transport: string
  response_reconstructed: boolean
  response_complete: boolean
  capture_error: Record<string, unknown> | null
  tokens_system_prompt: number
  tokens_tool_definitions: number
  tokens_tool_results: number
  tokens_file_contents: number
  tokens_conversation_history: number
  tokens_current_user_message: number
  tokens_assistant_prefill: number
  tokens_uncategorized: number
  tokens_total_input: number
  tokens_total_output: number
  tokens_output_text: number
  tokens_output_thinking: number
  provider_input_tokens: number | null
  provider_output_tokens: number | null
  provider_reasoning_tokens: number | null
  cache_read_tokens: number | null
  cache_creation_tokens: number | null
  cache_write_tokens: number | null
  uncached_input_tokens: number | null
  ordinary_input_tokens: number | null
  cache_hit_pct: number | null
  usage_extra: Record<string, unknown> | null
  provider_request_id: string | null
  previous_provider_request_id: string | null
  provider_conversation_id: string | null
  logical_turn_id: string | null
  invocation_seq: number | null
  lineage_status: string
  identity_metadata: Record<string, unknown>
  observed_input_tokens: number
  reconstructed_input_tokens: number | null
  unattributed_input_tokens: number | null
  input_token_variance: number | null
  context_coverage_pct: number | null
  context_reconstruction_status: string
  session_seq: number | null
  tokenizer: string
  raw_request_body?: string | null
  raw_response_body?: string | null
  response_events?: unknown[] | null
}

export interface LogicalRequest {
  id: string
  session_id: string | null
  provider: string
  agent: string | null
  model: string | null
  endpoint: string
  transport: string
  provider_conversation_id: string | null
  logical_turn_id: string | null
  parent_logical_request_id: string | null
  started_at: string
  updated_at: string
  state: 'complete' | 'incomplete' | 'error'
  grouping_confidence: string
  grouping_metadata: Record<string, unknown>
  invocation_count: number
  peak_context_tokens: number | null
  final_context_tokens: number | null
  cumulative_input_tokens: number | null
  cumulative_cached_tokens: number | null
  cumulative_cache_write_tokens: number | null
  cumulative_output_tokens: number | null
  cumulative_reasoning_tokens: number | null
  cache_hit_pct: number | null
  duration_ms: number | null
  status_code: number | null
}

export interface ContextSnapshotBlock {
  id: number
  request_id: string
  position: number
  source_request_id: string | null
  source_block_id: number | null
  direction: string
  message_index: number | null
  block_type: string
  category: string | null
  content: string | null
  content_purged: boolean
  token_count: number
  tool_name: string | null
  tool_call_id: string | null
  provenance: string
  attrs: Record<string, unknown>
}

export interface RequestContext {
  request_id: string
  invocation_seq: number | null
  selection?: 'final_invocation' | 'largest_reconstructed_snapshot'
  lineage: {
    provider_request_id: string | null
    previous_provider_request_id: string | null
    provider_conversation_id: string | null
    logical_turn_id: string | null
    lineage_status: string
  }
  accounting: {
    observed_input_tokens: number
    reconstructed_input_tokens: number | null
    provider_input_tokens: number | null
    unattributed_input_tokens: number | null
    input_token_variance: number | null
    context_coverage_pct: number | null
    cache_read_tokens: number | null
    cache_write_tokens: number | null
    uncached_input_tokens: number | null
    ordinary_input_tokens: number | null
    status: string
  }
  composition: {
    total_tokens: number
    visible_block_tokens: number
    by_category: Record<string, number>
  }
  tools: ToolStat[]
  blocks: ContextSnapshotBlock[]
}

export interface CategoryStats {
  tokens: number
  pct: number
}

export interface LatencyStats {
  avg_ms: number | null
  p50_ms: number | null
  p95_ms: number | null
  p99_ms: number | null
  min_ms: number | null
  max_ms: number | null
}

export interface SessionTiming {
  first_request_at: string | null
  last_request_at: string | null
  elapsed_ms: number | null
  active_duration_ms: number | null
}

export interface Stats {
  request_count: number
  model_call_count: number
  logical_request_count: number
  tokens_total_input: number
  tokens_total_output: number
  /** Output split: generated text vs. reasoning. Both roll up into tokens_total_output. */
  tokens_output_text: number
  tokens_output_thinking: number
  by_category: Record<string, CategoryStats>
  by_provider: Record<string, number>
  by_agent: Record<string, number>
  by_model: Record<string, number>
  latency: LatencyStats
  by_status: Record<string, number>
  /** Count of requests with status_code >= 400. */
  error_count: number
  unknown_status_count: number
  session_timing: SessionTiming
}

export interface TimelineBucket {
  bucket: string
  request_count: number
  tokens_total_input: number
}

export interface SessionSummaryEntry {
  type: 'session' | 'gap'
  session_id: string | null
  name: string | null
  started_at: string
  ended_at: string | null
  /** null while active (no ended_at yet). */
  duration_ms: number | null
  is_active: boolean
  request_count: number
  model_call_count: number
  tokens_in: number
  tokens_out: number
  tokens_system_prompt: number
  tokens_tool_definitions: number
  tokens_tool_results: number
  tokens_file_contents: number
  tokens_conversation_history: number
  tokens_current_user_message: number
  tokens_assistant_prefill: number
  tokens_uncategorized: number
}

export interface ToolStat {
  tool_name: string
  definition_tokens: number
  result_tokens: number
}

export interface RequestBlock {
  id: number
  direction: 'input' | 'output'
  position: number
  message_index: number | null
  block_type: string
  category: string | null
  content: string | null
  content_purged: boolean
  token_count: number
  tool_name: string | null
  tool_call_id: string | null
  attrs: Record<string, unknown>
  linked_call_id: number | null
  linked_definition_id: number | null
  linked_previous_message_id: number | null
  /** Earliest session_seq (within the same session) this block's content was
   *  first seen at; null with no session, or no content to hash (e.g. structural blocks). */
  first_seen_session_seq: number | null
}

export interface ProxyStatus {
  running: boolean
  port: number
  cert_installed: boolean
}

// ---- Session API ----------------------------------------------------------

export const sessionsApi = {
  list: () => apiFetch<{ sessions: Session[] }>('/sessions'),
  get: (id: string) => apiFetch<{ session: Session; stats: Stats }>(`/sessions/${id}`),
  create: (name: string) =>
    apiFetch<{ session: Session; warning: string | null }>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  end: (id: string) =>
    apiFetch<{ session: Session }>(`/sessions/${id}/end`, { method: 'POST' }),
  rename: (id: string, name: string) =>
    apiFetch<{ session: Session }>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),
  delete: (id: string, deleteRequests = false) =>
    apiFetch<{ deleted: string }>(`/sessions/${id}?delete_requests=${deleteRequests}`, { method: 'DELETE' }),
}

// ---- Requests API ---------------------------------------------------------

export const requestsApi = {
  list: (params: { session_id?: string; provider?: string; agent?: string; model?: string; q?: string; status_category?: string; sort_by?: string; sort_dir?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params.session_id) qs.set('session_id', params.session_id)
    if (params.provider) qs.set('provider', params.provider)
    if (params.agent) qs.set('agent', params.agent)
    if (params.model) qs.set('model', params.model)
    if (params.q) qs.set('q', params.q)
    if (params.status_category) qs.set('status_category', params.status_category)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    if (params.sort_by != null) qs.set('sort_by', params.sort_by)
    if (params.sort_dir != null) qs.set('sort_dir', params.sort_dir)
    return apiFetch<{ requests: Request[] }>(`/requests?${qs}`)
  },
  get: (id: string) => apiFetch<{ request: Request }>(`/requests/${id}`),
  blocks: (id: string) =>
    apiFetch<{ session_seq: number | null; blocks: RequestBlock[] }>(`/requests/${id}/blocks`),
  context: (id: string) => apiFetch<RequestContext>(`/requests/${id}/context`),
}

export const logicalRequestsApi = {
  list: (params: { session_id?: string; provider?: string; agent?: string; model?: string; q?: string; status_category?: string; sort_by?: string; sort_dir?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    if (params.session_id) qs.set('session_id', params.session_id)
    if (params.provider) qs.set('provider', params.provider)
    if (params.agent) qs.set('agent', params.agent)
    if (params.model) qs.set('model', params.model)
    if (params.q) qs.set('q', params.q)
    if (params.status_category) qs.set('status_category', params.status_category)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    if (params.sort_by) qs.set('sort_by', params.sort_by)
    if (params.sort_dir) qs.set('sort_dir', params.sort_dir)
    return apiFetch<{ logical_requests: LogicalRequest[] }>(`/logical-requests?${qs}`)
  },
  get: (id: string) => apiFetch<{ logical_request: LogicalRequest; invocations: Request[]; context: RequestContext | null }>(`/logical-requests/${id}`),
}

// ---- Stats API ------------------------------------------------------------

export const statsApi = {
  overview: () => apiFetch<Stats>('/stats/overview'),
  session: (id: string) => apiFetch<Stats>(`/stats/session/${id}`),
  timeline: (params: { session_id?: string; bucket?: string }) => {
    const q = new URLSearchParams()
    if (params.session_id) q.set('session_id', params.session_id)
    if (params.bucket) q.set('bucket', params.bucket)
    return apiFetch<{ timeline: TimelineBucket[] }>(`/stats/timeline?${q}`)
  },
  tools: (sessionId?: string, requestId?: string) => {
    const q = new URLSearchParams()
    if (sessionId) q.set('session_id', sessionId)
    if (requestId) q.set('request_id', requestId)
    return apiFetch<{ tools: ToolStat[] }>(`/stats/tools?${q}`)
  },
  sessionsSummary: () => apiFetch<{ entries: SessionSummaryEntry[] }>('/stats/sessions-summary'),
}

// ---- Proxy API ------------------------------------------------------------

export const proxyApi = {
  status: () => apiFetch<ProxyStatus>('/proxy/status'),
  start: () => apiFetch<{ status: string }>('/proxy/start', { method: 'POST' }),
  stop: () => apiFetch<{ status: string }>('/proxy/stop', { method: 'POST' }),
  installCert: () =>
    apiFetch<{ success: boolean; message: string }>('/proxy/install-cert', { method: 'POST' }),
}

// ---- Tokenize API ---------------------------------------------------------

export const tokenizeApi = {
  tokenize: (texts: string[]) =>
    apiFetch<{ results: string[][] }>('/tokenize', {
      method: 'POST',
      body: JSON.stringify({ texts }),
    }),
}
