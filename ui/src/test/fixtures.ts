import type { Request, RequestBlock } from '../api/client'

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
    linked_call_id: null,
    linked_definition_id: null,
    linked_previous_message_id: null,
    first_seen_session_seq: 1,
    ...overrides,
  }
}

export function makeRequest(overrides: Partial<Request> = {}): Request {
  return {
    id: 'request-1', session_id: null, timestamp: '2026-09-09T10:00:00', provider: 'openai', model: 'gpt-test', agent: 'codex', endpoint: '/v1/responses',
    duration_ms: 250, ttft_ms: 80, status_code: 200, transport: 'https', response_transport: 'sse', response_reconstructed: false,
    response_complete: true, capture_error: null, provider_response_id: null, predecessor_response_id: null, invocation_outcome: 'completed',
    context_fidelity: 'complete', context_notes: [], tokens_system_prompt: 20, tokens_tool_definitions: 0, tokens_tool_results: 0,
    tokens_file_contents: 0, tokens_conversation_history: 0, tokens_current_user_message: 10, tokens_assistant_prefill: 0, tokens_uncategorized: 0,
    tokens_total_input: 30, tokens_total_output: 5, tokens_output_text: 5, tokens_output_thinking: 0, provider_input_tokens: 30,
    provider_output_tokens: 5, provider_reasoning_tokens: null, cache_read_tokens: 0, cache_creation_tokens: 0, usage_extra: null, session_seq: 1,
    tokenizer: 'o200k_base', context_accounting: { visible_input_tokens: 30, provider_input_tokens: 30, cached_input_tokens: 0, cache_write_tokens: 0, unattributed_difference: 0, visible_coverage_pct: 100, cached_share_pct: 0 },
    request_body: '{"prompt":"hello"}', response_body: '{"text":"hi"}', response_events: null,
    ...overrides,
  }
}
