import { subscribeToLocalWrites } from '../db/local'
import { syncOnce } from './synchronizer'

/** docs/06 §7: every 60 s while the app is in front. */
const SYNC_INTERVAL_MS = 60_000

/** docs/06 §7: 2 s after writing, so one form does not mean one push per keystroke. */
const WRITE_DEBOUNCE_MS = 2_000

/**
 * Starts the synchronization triggers and returns the function that stops them:
 * on start, on the browser's `online` event, every 60 s while the document is
 * visible, and 2 s after a local write (debounced, so a burst of writes is one
 * run).
 *
 * Scopes runInFlight and followUpRequested to each startSynchronizer() call (R3, #163)
 * so that stopping prevents any follow-up from starting after stop and restarts start clean.
 */
export function startSynchronizer(): () => void {
  let stopped = false
  let runInFlight = false
  let followUpRequested = false

  function startRun(): void {
    if (stopped) return
    if (runInFlight) {
      followUpRequested = true
      return
    }
    runInFlight = true
    void syncOnce()
      .catch((error: unknown) => {
        console.error('a synchronization run failed unexpectedly', error)
      })
      .finally(() => {
        runInFlight = false
        if (stopped || !followUpRequested) return
        followUpRequested = false
        startRun()
      })
  }

  startRun()

  const onOnline = (): void => {
    startRun()
  }
  window.addEventListener('online', onOnline)

  const interval = window.setInterval(() => {
    if (document.visibilityState === 'visible') startRun()
  }, SYNC_INTERVAL_MS)

  let debounce: number | null = null
  const stopWatchingWrites = subscribeToLocalWrites(() => {
    if (stopped) return
    if (debounce !== null) window.clearTimeout(debounce)
    debounce = window.setTimeout(() => {
      debounce = null
      startRun()
    }, WRITE_DEBOUNCE_MS)
  })

  return () => {
    stopped = true
    followUpRequested = false
    window.removeEventListener('online', onOnline)
    window.clearInterval(interval)
    stopWatchingWrites()
    if (debounce !== null) window.clearTimeout(debounce)
  }
}
