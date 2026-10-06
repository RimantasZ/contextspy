import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeRequest } from '../../test/fixtures'
import { RequestSummaryHeader } from './RequestSummaryHeader'

const noop = () => {}

describe('RequestSummaryHeader purpose', () => {
  it('shows the inferred purpose and a one-line summary', () => {
    const request = makeRequest({
      purpose: 'tool_continuation',
      purpose_detail: { trailing_tool_results: ['Read'], response: { kind: 'tool_calls', tool_calls: ['Edit'] } },
    })
    render(<RequestSummaryHeader request={request} onBack={noop} onDirection={noop} />)
    expect(screen.getByText('Tool continuation')).toBeTruthy()
    expect(screen.getByText('results from Read → calls Edit')).toBeTruthy()
  })

  it('shows nothing for a request that was never classified', () => {
    render(<RequestSummaryHeader request={makeRequest()} onBack={noop} onDirection={noop} />)
    expect(screen.queryByText('Tool continuation')).toBeNull()
    expect(screen.queryByText('User turn')).toBeNull()
    expect(screen.queryByText('Unknown purpose')).toBeNull()
  })

  it('tolerates a reserved purpose without detail', () => {
    render(<RequestSummaryHeader request={makeRequest({ purpose: 'compaction' })} onBack={noop} onDirection={noop} />)
    expect(screen.getByText('Compaction')).toBeTruthy()
  })
})
