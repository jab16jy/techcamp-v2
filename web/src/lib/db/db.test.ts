import { afterEach, describe, expect, it, vi } from 'vitest'

/**
 * `requestPersistentStorage` is once-per-session module state, so each test
 * imports a fresh copy of the module rather than resetting a shared flag.
 */
afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
})

describe('requestPersistentStorage', () => {
  it('asks the browser once, so a second call is a no-op', async () => {
    const persist = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('navigator', { storage: { persist } })
    const { requestPersistentStorage } = await import('./db')

    requestPersistentStorage()
    requestPersistentStorage()

    expect(persist).toHaveBeenCalledTimes(1)
  })

  it('is a no-op where the browser has no storage manager, and never throws', async () => {
    vi.stubGlobal('navigator', {})
    const { requestPersistentStorage } = await import('./db')

    expect(() => {
      requestPersistentStorage()
    }).not.toThrow()
  })
})
