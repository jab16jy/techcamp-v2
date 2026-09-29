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

  it('absorbs a refused persist(), which no caller can catch because it returns void', async () => {
    // A browser may refuse the request (no engagement, a non-secure context, a
    // user who said no). Nothing in the app can act on that and the caller gets
    // no promise, so an unhandled rejection would be the only visible symptom of
    // a purely best-effort call (#144).
    //
    // What is observed is whether a rejection handler was attached to the
    // promise `persist()` returned, because that is what decides whether the
    // refusal ever reaches the global unhandled-rejection report. A
    // `process.on('unhandledRejection')` listener would say it more directly,
    // but `tsconfig.app.json` pins `types` to the browser on purpose (the app
    // is browser-only), so nothing under `src/` may reach for Node globals.
    let handlerAttached = false
    const persist = (): Promise<boolean> => {
      const refused = Promise.reject(new Error('persistence refused'))
      return Object.assign(refused, {
        catch: (onRejected: (reason: unknown) => unknown) => {
          handlerAttached = true
          return refused.then(undefined, onRejected)
        },
      })
    }
    vi.stubGlobal('navigator', { storage: { persist } })
    const { requestPersistentStorage } = await import('./db')

    requestPersistentStorage()

    expect(handlerAttached).toBe(true)
  })
})
