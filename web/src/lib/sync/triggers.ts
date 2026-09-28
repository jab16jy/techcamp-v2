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
 * The interval checks `visibilityState` on every tick rather than subscribing
 * to visibility changes: a hidden tab has no business talking to the API, and
 * iOS has no Background Sync, so the app syncs in the foreground and nothing
 * more (docs/06 §7, ADR-0005).
 */
export function startSynchronizer(): () => void {
  void syncOnce()

  const onOnline = (): void => {
    void syncOnce()
  }
  window.addEventListener('online', onOnline)

  const interval = window.setInterval(() => {
    if (document.visibilityState === 'visible') void syncOnce()
  }, SYNC_INTERVAL_MS)

  let debounce: number | null = null
  const stopWatchingWrites = subscribeToLocalWrites(() => {
    if (debounce !== null) window.clearTimeout(debounce)
    debounce = window.setTimeout(() => {
      debounce = null
      void syncOnce()
    }, WRITE_DEBOUNCE_MS)
  })

  return () => {
    window.removeEventListener('online', onOnline)
    window.clearInterval(interval)
    stopWatchingWrites()
    if (debounce !== null) window.clearTimeout(debounce)
  }
}
