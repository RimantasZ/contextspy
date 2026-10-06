import { describe, expect, it } from 'vitest'
import { purposeLabel, purposeSummary } from './purpose'

describe('purposeLabel', () => {
  it('labels known purposes, passes unknown ones through and hides unclassified requests', () => {
    expect(purposeLabel('user_turn')).toBe('User turn')
    expect(purposeLabel('tool_continuation')).toBe('Tool continuation')
    expect(purposeLabel('compaction')).toBe('Compaction')
    expect(purposeLabel('some_future_purpose')).toBe('some_future_purpose')
    expect(purposeLabel(null)).toBeNull()
  })
})

describe('purposeSummary', () => {
  it('describes trailing results and the response', () => {
    expect(purposeSummary({ purpose_detail: { trailing_tool_results: ['Read', 'Grep'], response: { kind: 'tool_calls', tool_calls: ['Edit'] } } }))
      .toBe('results from Read, Grep → calls Edit')
  })

  it('covers every response kind', () => {
    expect(purposeSummary({ purpose_detail: { response: { kind: 'final_text' } } })).toBe('final answer')
    expect(purposeSummary({ purpose_detail: { response: { kind: 'empty' } } })).toBe('no visible output')
    expect(purposeSummary({ purpose_detail: { response: { kind: 'mixed', tool_calls: ['Bash'] } } })).toBe('text and calls Bash')
    expect(purposeSummary({ purpose_detail: { response: { kind: 'tool_calls' } } })).toBe('calls tools')
  })

  it('returns null without detail', () => {
    expect(purposeSummary({ purpose_detail: null })).toBeNull()
    expect(purposeSummary({ purpose_detail: { has_user_text: true } })).toBeNull()
  })
})
