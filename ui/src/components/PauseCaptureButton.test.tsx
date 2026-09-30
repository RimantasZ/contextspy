import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { PauseCaptureButton } from './PauseCaptureButton'

const mutate = vi.fn()
let paused = false

vi.mock('../api/hooks', () => ({
  useProxyStatus: () => ({ data: { running: true, port: 8080, cert_installed: true, paused } }),
  useSetCapturePaused: () => ({ mutate, isPending: false }),
}))

describe('PauseCaptureButton', () => {
  it('offers to pause when capturing', () => {
    paused = false
    render(<PauseCaptureButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause capture' }))
    expect(mutate).toHaveBeenCalledWith(true)
  })

  it('shows highlighted resume state when paused', () => {
    paused = true
    render(<PauseCaptureButton />)
    const btn = screen.getByRole('button', { name: 'Resume capture' })
    expect(btn.getAttribute('aria-pressed')).toBe('true')
    expect(btn.className).toContain('app-button-primary')
    fireEvent.click(btn)
    expect(mutate).toHaveBeenCalledWith(false)
  })
})
