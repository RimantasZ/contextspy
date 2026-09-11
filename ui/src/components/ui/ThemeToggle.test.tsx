import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { ThemeToggle, useTheme } from './ThemeToggle'

function ThemeHarness() {
  const { theme, toggleTheme } = useTheme()
  return <ThemeToggle theme={theme} onToggle={toggleTheme} />
}

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.dataset.theme = 'light'
  })

  it('switches the document to dark mode and persists the preference', async () => {
    render(<ThemeHarness />)
    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'))
    expect(localStorage.getItem('contextspy-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Switch to light mode' })).toBeTruthy()
  })

  it('exposes an explicit accessible name in compact mode', () => {
    render(<ThemeToggle theme="dark" onToggle={() => {}} showLabel={false} />)
    expect(screen.getByRole('button', { name: 'Switch to light mode' })).toBeTruthy()
  })
})
