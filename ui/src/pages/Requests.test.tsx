import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Requests from './Requests'

const { useRequestsMock } = vi.hoisted(() => ({
  useRequestsMock: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  useRequests: useRequestsMock,
  useSessions: () => ({ data: { sessions: [] } }),
  useStatsOverview: () => ({
    data: {
      by_provider: { openai_chatgpt: 3, anthropic_vertex: 1 },
      by_agent: { codex: 3, 'claude-code': 1, unknown: 1 },
      by_model: {},
    },
  }),
}))

function renderRequests() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Requests />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useRequestsMock.mockReset()
  useRequestsMock.mockReturnValue({ data: { requests: [] }, isLoading: false })
})

describe('Requests filters', () => {
  it('uses captured provider and agent values and sends the selected values to the API', async () => {
    renderRequests()

    const providerSelect = screen.getByRole('combobox', { name: 'Filter by provider' }) as HTMLSelectElement
    const agentSelect = screen.getByRole('combobox', { name: 'Filter by agent' }) as HTMLSelectElement

    expect(Array.from(providerSelect.options, (option) => option.value)).toEqual([
      '',
      'anthropic_vertex',
      'openai_chatgpt',
    ])
    expect(Array.from(agentSelect.options, (option) => option.value)).toEqual([
      '',
      'claude-code',
      'codex',
      'unknown',
    ])

    await userEvent.selectOptions(providerSelect, 'openai_chatgpt')
    await userEvent.selectOptions(agentSelect, 'codex')

    await waitFor(() => expect(useRequestsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ provider: 'openai_chatgpt', agent: 'codex' }),
    ))
  })
})
