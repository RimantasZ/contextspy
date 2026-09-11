import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeRequest } from '../test/fixtures'
import { RequestTable } from './RequestTable'

describe('RequestTable', () => {
  it('publishes sorting semantics and row navigation', async () => {
    const navigate = vi.fn()
    render(<RequestTable requests={[makeRequest()]} onRowClick={navigate} />)
    const inputHeader = screen.getByRole('columnheader', { name: /Input/i })
    expect(inputHeader.getAttribute('aria-sort')).toBe('none')
    await userEvent.click(screen.getByRole('button', { name: 'Input' }))
    expect(inputHeader.getAttribute('aria-sort')).toBe('ascending')
    await userEvent.click(screen.getAllByText('gpt-test')[0])
    expect(navigate).toHaveBeenCalledWith('request-1')
  })

  it('provides responsive detail disclosure with secondary fields', async () => {
    render(<RequestTable requests={[makeRequest({ tokens_output_thinking: 4 })]} onRowClick={() => {}} />)
    await userEvent.click(screen.getAllByRole('button', { name: /Show request details/i })[0])
    expect(screen.getAllByText('Output text').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Thinking').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Agent').length).toBeGreaterThan(0)
  })
})
