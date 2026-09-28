import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getPendingCount, subscribePendingCount } from '../db/live'
import { db } from '../db/db'
import { saveLogbookEntry } from '../db/local'
import { resetLocalDb } from '../db/testDb'
import { uuidv7 } from '../db/ids'
import { setSession } from '../api/session'
import type { LogbookEntryDraft } from '../db/local'
import { startSynchronizer } from './triggers'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

function draft(): LogbookEntryDraft {
  return {
    id: uuidv7(),
    org_id: ORG_ID,
    plot_id: PLOT_ID,
    crop_cycle_id: null,
    kind: 'harvest',
    occurred_on: '2026-09-28',
    quantity: null,
    unit: null,
    cost_cop: null,
    yield_kg: 120,
    sold_kg: null,
    sale_price_cop_per_kg: null,
    labor_days: null,
    irrigation_mm: null,
    alert_id: null,
    notes: null,
    created_by: null,
  }
}

/**
 * Counts synchronization runs by counting pulls. Every run ends by pulling, the
 * stubbed pull always answers `has_more: false`, and a push may or may not
 * happen depending on what the outbox held — so pulls count runs exactly,
 * whichever it is.
 */
function stubSyncApi(): { runs: () => number } {
  let pulls = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (!url.endsWith('/push')) {
        pulls += 1
        return json({ changes: [], next_since: 0, has_more: false })
      }
      const request = JSON.parse(String(init?.body)) as { changes: { id: string }[] }
      return json({ results: request.changes.map((change) => ({ id: change.id, status: 'applied', server_version: 1 })) })
    }),
  )
  return { runs: () => pulls }
}

/** A fresh counting stub, for asserting on runs started later in a test. */
function api(): { runs: () => number } {
  return stubSyncApi()
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

/**
 * Drains the microtask queue without moving the clock. A run walks through
 * several Dexie reads, each one a real (unfaked) `setImmediate` round trip, so
 * one hop is not enough to know a run has finished.
 *
 * `vi.waitFor` is deliberately NOT used to wait for a run: it advances faked
 * timers while polling, which would move the clock out from under the debounce
 * arithmetic these tests are about.
 */
async function flush(times = 30): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await vi.advanceTimersByTimeAsync(0)
    await nextMacrotask()
  }
}

/**
 * One turn of the real macrotask queue, without moving the faked clock. Dexie
 * schedules its transaction work with `setImmediate`, which these tests
 * deliberately leave unfaked (faking it deadlocks every read), and a microtask
 * yield would not let that run. `MessageChannel` is the browser-native way to
 * ask for one macrotask turn; `setTimeout` cannot be used because it is faked,
 * and `setImmediate` is not declared: `tsconfig.app.json` pins `types` to the
 * browser, so nothing under `src/` may name a Node global.
 */
function nextMacrotask(): Promise<void> {
  return new Promise((resolve) => {
    const channel = new MessageChannel()
    channel.port1.onmessage = () => {
      channel.port1.close()
      resolve()
    }
    channel.port2.postMessage(null)
  })
}

/**
 * Waits for `count` runs to have reached the network, without moving the clock,
 * and then lets the last one finish. Both halves matter: the count alone leaves
 * the run in flight, and the next trigger would be swallowed by single-flight,
 * which is correct behavior and would make these tests lie.
 */
async function waitForRuns(api: { runs: () => number }, count: number): Promise<void> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (api.runs() >= count) {
      await flush()
      return
    }
    await flush(1)
  }
  throw new Error(`expected ${count} synchronization runs, saw ${api.runs()}`)
}

/** Polls `ready` without moving the clock, for the live-query assertions. */
async function waitFor(ready: () => boolean, times = 200): Promise<void> {
  for (let attempt = 0; attempt < times; attempt += 1) {
    if (ready()) return
    await flush(1)
  }
  throw new Error('condition never became true')
}

const realVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState')

function setVisibility(state: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
}

beforeEach(async () => {
  await resetLocalDb()
  setSession('test-token', null)
  setVisibility('visible')
  // Only the clock the triggers use: fake-indexeddb schedules its transactions
  // with `setImmediate`, and faking that deadlocks every Dexie read.
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
})

afterEach(() => {
  while (runningStops.length > 0) runningStops.pop()?.()
  vi.useRealTimers()
  if (realVisibility !== undefined) Object.defineProperty(document, 'visibilityState', realVisibility)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/**
 * Every synchronizer started in this file, stopped after each test even when an
 * assertion fails. A leaked trigger keeps its write subscription alive, and the
 * next test's writes would then schedule a run that belongs to nobody.
 */
const runningStops: (() => void)[] = []

function start(): () => void {
  const stop = startSynchronizer()
  runningStops.push(stop)
  return stop
}

describe('startSynchronizer', () => {
  it('runs once on start, and again when the browser comes back online', async () => {
    const api = stubSyncApi()
    await saveLogbookEntry(draft())

    start()
    await waitForRuns(api, 1)

    window.dispatchEvent(new Event('online'))
    await waitForRuns(api, 2)
  })

  it('waits 2 s after the LAST write, so a burst is one run and never an early one', async () => {
    const api = stubSyncApi()
    await saveLogbookEntry(draft())
    start()
    await waitForRuns(api, 1)

    // First write at T, second at T+1.5 s. The second restarts the 2 s window,
    // so the run is due at T+3.5 s and NOT at T+2 s. Asserting the window is
    // what makes this a debounce test: without a reset the first write's own
    // deadline would fire during the step below, and single-flight would hide
    // the difference by collapsing the extra runs instead.
    await saveLogbookEntry(draft())
    await vi.advanceTimersByTimeAsync(1_500)
    await saveLogbookEntry(draft())

    await vi.advanceTimersByTimeAsync(1_000) // T+2.5 s, past the first deadline
    await flush()
    expect(api.runs()).toBe(1)

    await vi.advanceTimersByTimeAsync(500) // T+3 s
    await flush()
    expect(api.runs()).toBe(1)

    await vi.advanceTimersByTimeAsync(500) // T+3.5 s, the reset window closed
    // Two writes inside the window are one run, not two.
    await waitForRuns(api, 2)
    await flush()
    expect(api.runs()).toBe(2)
  })

  it('runs once more for a write that lands while a run is in flight', async () => {
    // The first run is held open in its push. A second entry is written while it
    // is still going, and its debounce fires before the run finishes: `syncOnce`
    // is single-flight, so that call would hand back the running promise and the
    // new change would wait for the next 60 s tick, an `online` event, or a
    // restart.
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    let pushes = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith('/push')) {
          pushes += 1
          if (pushes === 1) await gate
          const request = JSON.parse(String(init?.body)) as { changes: { id: string }[] }
          return json({ results: request.changes.map((c) => ({ id: c.id, status: 'applied', server_version: 1 })) })
        }
        return json({ changes: [], next_since: 0, has_more: false })
      }),
    )
    await saveLogbookEntry(draft())
    start()
    // The first run is now inside its held push.
    for (let attempt = 0; attempt < 200 && pushes === 0; attempt += 1) await flush(1)
    expect(pushes).toBe(1)

    await saveLogbookEntry(draft())
    await vi.advanceTimersByTimeAsync(2_000)
    await flush()
    // Still one: the follow-up waits for the run in flight.
    expect(pushes).toBe(1)

    release()
    await waitFor(() => pushes === 2)
    await flush()
    // Exactly one follow-up, and it carried the change written mid-run.
    expect(pushes).toBe(2)
    expect(await db.outbox.count()).toBe(0)
  })

  it('ticks every 60 s only while the document is visible', async () => {
    const api = stubSyncApi()
    await saveLogbookEntry(draft())
    setVisibility('hidden')
    start()
    await waitForRuns(api, 1)

    await vi.advanceTimersByTimeAsync(60_000)
    await flush()
    expect(api.runs()).toBe(1)

    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(60_000)
    await waitForRuns(api, 2)
  })

  it('contains a run that throws, so a trigger never leaves a rejected promise', async () => {
    // A malformed page the guards do not model: `changes` is a number, so
    // iterating it throws a plain TypeError that escapes `run()`. A timer or an
    // event handler has no caller to catch that, so the trigger must.
    const reported: unknown[] = []
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
      reported.push(args)
    })
    stubSyncApi()
    await saveLogbookEntry(draft())
    start()
    await waitForRuns(api(), 1)
    // Replace the api for the next run with one that answers a broken page.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        new Response(
          JSON.stringify(String(input).endsWith('/push') ? { results: [] } : { changes: 42, next_since: 0, has_more: false }),
          { status: 200 },
        ),
      ),
    )

    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    await flush()

    // Contained, and still visible: swallowed silently would be a fault nobody
    // can diagnose.
    expect(reported).toHaveLength(1)
    // The trigger survived it and keeps working.
    const recovered = stubSyncApi()
    window.dispatchEvent(new Event('online'))
    await waitForRuns(recovered, 1)
  })

  it('stops every trigger it installed', async () => {
    const api = stubSyncApi()
    await saveLogbookEntry(draft())
    const stop = start()
    await waitForRuns(api, 1)

    stop()
    window.dispatchEvent(new Event('online'))
    await saveLogbookEntry(draft())
    await vi.advanceTimersByTimeAsync(120_000)
    await flush()

    // No run came from the event, the interval or the write after stopping.
    expect(api.runs()).toBe(1)
  })
})

describe('pendingCount', () => {
  it('follows the outbox, so the indicator counts what is really waiting to be pushed', async () => {
    const seen: number[] = []
    const unsubscribe = subscribePendingCount(() => seen.push(getPendingCount()))

    try {
      await waitFor(() => getPendingCount() === 0)

      await saveLogbookEntry(draft())
      await waitFor(() => getPendingCount() === 1)

      await saveLogbookEntry(draft())
      await waitFor(() => getPendingCount() === 2)
      // Every transition was published to the listener, not just the last one.
      expect(seen).toContain(1)
      expect(seen).toContain(2)
    } finally {
      unsubscribe()
    }
  })

  it('stops publishing once nobody is listening', async () => {
    const api = stubSyncApi()
    const seen: number[] = []
    const unsubscribe = subscribePendingCount(() => seen.push(getPendingCount()))
    await vi.waitFor(() => expect(getPendingCount()).toBe(0))
    const before = seen.length

    unsubscribe()
    await saveLogbookEntry(draft())
    await flush()

    expect(seen).toHaveLength(before)
    expect(api.runs()).toBe(0)
  })
})
