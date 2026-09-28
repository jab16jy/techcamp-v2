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
/**
 * The only way a trigger starts a run.
 *
 * A timer or an event handler has no caller to catch a rejected promise, so a run
 * that throws for a reason the run's own guards do not model would become a
 * global unhandled rejection from a background trigger (#147). It is contained
 * here and still reported: swallowing it would leave a fault nobody can
 * diagnose. The defined failures do not come through this path at all, since
 * `syncOnce` turns them into a `stopped` outcome.
 */
function startRun(): void {
  // `syncOnce` is single-flight: calling it while a run is in flight returns
  // that run, so a change queued after the run took its pending snapshot would
  // never be pushed by this call. Remember that it happened and run exactly
  // once more when the current run ends, instead of leaving the write to wait
  // for the next 60 s tick, the next `online` event, or a restart (RNF-01: the
  // change is on the phone and owed to the server).
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
      if (!followUpRequested) return
      // One boolean, so a burst of writes during a run is still one follow-up:
      // that run pushes all of them.
      followUpRequested = false
      startRun()
    })
}

let runInFlight = false
let followUpRequested = false

export function startSynchronizer(): () => void {
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
    if (debounce !== null) window.clearTimeout(debounce)
    debounce = window.setTimeout(() => {
      debounce = null
      startRun()
    }, WRITE_DEBOUNCE_MS)
  })

  return () => {
    window.removeEventListener('online', onOnline)
    window.clearInterval(interval)
    stopWatchingWrites()
    if (debounce !== null) window.clearTimeout(debounce)
  }
}
