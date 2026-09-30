import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useCompactRequestCards } from './requestViewState'

describe('request card density preferences', () => {
  it('keeps recent sessions and evicts the oldest after 32 choices', () => {
    const prefix = `density-${Date.now()}-`
    for (let index = 0; index < 32; index++) {
      const view = renderHook(() => useCompactRequestCards(`${prefix}${index}`))
      act(() => view.result.current[1](false))
      view.unmount()
    }
    // Read the first session again, making the second session the least recent.
    const recent = renderHook(() => useCompactRequestCards(`${prefix}0`))
    expect(recent.result.current[0]).toBe(false)
    recent.unmount()
    const added = renderHook(() => useCompactRequestCards(`${prefix}32`))
    act(() => added.result.current[1](false))
    added.unmount()
    const retained = renderHook(() => useCompactRequestCards(`${prefix}0`))
    const evicted = renderHook(() => useCompactRequestCards(`${prefix}1`))
    expect(retained.result.current[0]).toBe(false)
    expect(evicted.result.current[0]).toBe(true)
    retained.unmount()
    evicted.unmount()
  })
})
