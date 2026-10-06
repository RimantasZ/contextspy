import type { BlockOccurrences, Request, RequestBlock } from '../api/client'

export function makeBlock(overrides: Partial<RequestBlock> = {}): RequestBlock {
  return {
    id: 1,
    direction: 'input',
    position: 0,
    message_index: 0,
    block_type: 'user_message',
    category: 'current_user_message',
    content: 'hello world',
    content_purged: false,
    token_count: 10,
    tool_name: null,
    tool_call_id: null,
    attrs: {},
    source_key: null,
    activity: null,
    json_path: null,
    file_path: null,
    linked_call_id: null,
    linked_definition_id: null,
    linked_previous_message_id: null,
    first_seen_session_seq: 1,
    ...overrides,
  }
}

export function makeOccurrences(overrides: Partial<BlockOccurrences> = {}): BlockOccurrences {
  return {
    scope: 'conversation', requested_scope: 'conversation', scope_note: null,
    identity: { kind: 'content_hash', block_type: 'user_message', tool_name: null, source_key: 'user', activity: null },
    ranges: [{ from_position: 0, to_position: 2, from_seq: 12, to_seq: 14, request_count: 3, occurrence_count: 3 }],
    requests_sample: [
      { request_id: 'a', block_id: 1, position: 0, session_seq: 12, conversation_code: 'C1', token_count: 10, context_fidelity: 'complete', is_current: false },
      { request_id: 'request-1', block_id: 1, position: 1, session_seq: 13, conversation_code: 'C1', token_count: 10, context_fidelity: 'complete', is_current: true },
    ],
    totals: {
      occurrence_count: 3, request_count: 3, tokens_per_occurrence: 10, total_visible_tokens: 30, first_seen_session_seq: 12,
      last_seen_session_seq: 14, in_latest_request_of_scope: true, scope_request_count: 5, fidelity_counts: { complete: 3 },
    },
    ...overrides,
  }
}

export function makeRequest(overrides: Partial<Request> = {}): Request {
  return {
    id: 'request-1', session_id: null, timestamp: '2026-09-09T10:00:00', started_at: null, completed_at: '2026-09-09T10:00:00', started_at_source: 'estimated', provider: 'openai', model: 'gpt-test', agent: 'codex', endpoint: '/v1/responses',
    duration_ms: 250, ttft_ms: 80, status_code: 200, transport: 'https', response_transport: 'sse', response_reconstructed: false,
    response_complete: true, capture_error: null, provider_response_id: null, predecessor_response_id: null, invocation_outcome: 'completed',
    context_fidelity: 'complete', context_notes: [], tokens_system_prompt: 20, tokens_tool_definitions: 0, tokens_tool_results: 0,
    tokens_file_contents: 0, tokens_conversation_history: 0, tokens_current_user_message: 10, tokens_assistant_prefill: 0, tokens_uncategorized: 0,
    tokens_total_input: 30, tokens_total_output: 5, tokens_output_text: 5, tokens_output_thinking: 0, provider_input_tokens: 30,
    provider_output_tokens: 5, provider_reasoning_tokens: null, cache_read_tokens: 0, cache_creation_tokens: 0, usage_extra: null, session_seq: 1,
    tokenizer: 'o200k_base', purpose: null, purpose_detail: null, classifier_version: null, context_accounting: { visible_input_tokens: 30, provider_input_tokens: 30, cached_input_tokens: 0, cache_write_tokens: 0, unattributed_difference: 0, visible_coverage_pct: 100, cached_share_pct: 0 },
    request_body: '{"prompt":"hello"}', response_body: '{"text":"hi"}', response_events: null,
    ...overrides,
  }
}
