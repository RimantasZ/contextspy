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
  timestamp: string
  provider: string
  model: string | null
  agent: string | null
  endpoint: string
  duration_ms: number | null
  status_code: number | null
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
  provider_input_tokens: number | null
  provider_output_tokens: number | null
  cache_read_tokens: number | null
  cache_creation_tokens: number | null
  tokenizer: string
  raw_request_body?: string | null
  raw_response_body?: string | null
}

export interface CategoryStats {
  tokens: number
  pct: number
}

export interface Stats {
  request_count: number
  tokens_total_input: number
  tokens_total_output: number
  by_category: Record<string, CategoryStats>
  by_provider: Record<string, number>
  by_agent: Record<string, number>
}

export interface TimelineBucket {
  bucket: string
  request_count: number
  tokens_total_input: number
}

export interface ToolStat {
  tool_name: string
  definition_tokens: number
  result_tokens: number
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
  list: (params: { session_id?: string; provider?: string; agent?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams()
    if (params.session_id) q.set('session_id', params.session_id)
    if (params.provider) q.set('provider', params.provider)
    if (params.agent) q.set('agent', params.agent)
    if (params.limit != null) q.set('limit', String(params.limit))
    if (params.offset != null) q.set('offset', String(params.offset))
    return apiFetch<{ requests: Request[] }>(`/requests?${q}`)
  },
  get: (id: string) => apiFetch<{ request: Request }>(`/requests/${id}`),
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
