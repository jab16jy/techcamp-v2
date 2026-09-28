import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from '../db/db'
import { getCursor } from '../db/meta'
import { saveLogbookEntry } from '../db/local'
import { resetLocalDb } from '../db/testDb'
import { uuidv7 } from '../db/ids'
import { clearSession, setSession } from '../api/session'
import type { LogbookEntryDraft } from '../db/local'
import { syncOnce } from './synchronizer'
import type { PullResponse, PushRequest } from './types'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

function draft(overrides: Partial<LogbookEntryDraft> = {}): LogbookEntryDraft {
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
    ...overrides,
  }
}

const EMPTY_PULL: PullResponse = { changes: [], next_since: 0, has_more: false }

/** A fetch double that answers /push and /pull and records what it was sent. */
function stubSyncApi(options: {
  push?: (request: PushRequest, index: number) => Response | Promise<Response>
  pull?: (since: number, index: number) => Response | Promise<Response>
}): { pushRequests: PushRequest[]; pullQueries: string[] } {
  const pushRequests: PushRequest[] = []
  const pullQueries: string[] = []
  let pushIndex = 0
  let pullIndex = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/push')) {
        const request = JSON.parse(String(init?.body)) as PushRequest
        pushRequests.push(request)
        return (options.push ?? (() => json({ results: [] })))(request, pushIndex++)
      }
      pullQueries.push(url)
      const since = Number(new URL(url).searchParams.get('since'))
      return (options.pull ?? (() => json(EMPTY_PULL)))(since, pullIndex++)
    }),
  )
  return { pushRequests, pullQueries }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** A row as the server would send it back on a pull. */
function serverEntry(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    org_id: ORG_ID,
    plot_id: PLOT_ID,
    crop_cycle_id: null,
    kind: 'harvest',
    occurred_on: '2026-09-28',
    quantity: null,
    unit: null,
    cost_cop: null,
    yield_kg: 500,
    sold_kg: null,
    sale_price_cop_per_kg: null,
    labor_days: null,
    irrigation_mm: null,
    alert_id: null,
    notes: null,
    created_by: null,
    created_offline: false,
    client_updated_at: '2026-09-28T10:00:00.000Z',
    deleted_at: null,
    ...overrides,
  }
}

/**
 * Where `expireSession` sent the user. jsdom cannot navigate and its
 * `location.assign` is read-only, so the whole `location` is replaced for the
 * duration of the test and its own descriptor put back afterwards.
 */
const realLocation = Object.getOwnPropertyDescriptor(window, 'location')
let navigations: string[] = []

beforeEach(async () => {
  await resetLocalDb()
  setSession('test-token', null)
  navigations = []
  Object.defineProperty(window, 'location', {
    value: {
      assign: (url: string | URL) => {
        navigations.push(String(url))
      },
    },
    writable: true,
    configurable: true,
  })
})

afterEach(() => {
  if (realLocation !== undefined) Object.defineProperty(window, 'location', realLocation)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('syncOnce: push results', () => {
  it('clears the queued change and marks the row synced when the server applies it', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: () => json({ results: [{ id: entry.id, status: 'applied', server_version: 12 }] }),
      pull: () => json({ ...EMPTY_PULL, next_since: 12 }),
    })

    const outcome = await syncOnce()

    expect(outcome).toEqual({ status: 'synced', pushed: 1, pulled: 0 })
    const row = await db.logbookEntries.get(entry.id)
    expect(row?.syncState).toBe('synced')
    expect(row?.server_version).toBe(12)
    expect(await db.outbox.count()).toBe(0)
    expect(await getCursor()).toBe(12)
  })

  it('treats a duplicate as settled, because the server already holds this exact change', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: () => json({ results: [{ id: entry.id, status: 'duplicate', server_version: 3 }] }),
    })

    await syncOnce()

    expect((await db.logbookEntries.get(entry.id))?.syncState).toBe('synced')
    expect(await db.outbox.count()).toBe(0)
  })

  it('flags the row as conflict_overwritten and drops the queued change, leaving the winner to the pull', async () => {
    const entry = draft({ yield_kg: 120 })
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: () => json({ results: [{ id: entry.id, status: 'conflict_overwritten', server_version: 9 }] }),
    })

    await syncOnce()

    const row = await db.logbookEntries.get(entry.id)
    expect(row?.syncState).toBe('conflict_overwritten')
    // This device keeps its own content and does not claim the winner's version.
    expect(row?.yield_kg).toBe(120)
    expect(row?.server_version).toBeNull()
    expect(await db.outbox.count()).toBe(0)
  })

  it('keeps a rejected change with its error and never pushes it again', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    const { pushRequests } = stubSyncApi({
      push: () => json({ results: [{ id: entry.id, status: 'rejected', server_version: null, error: 'clock_skew' }] }),
    })

    await syncOnce()

    const [item] = await db.outbox.toArray()
    expect(item.status).toBe('rejected')
    expect(item.error).toBe('clock_skew')
    const row = await db.logbookEntries.get(entry.id)
    expect(row?.syncState).toBe('rejected')
    expect(row?.syncError).toBe('clock_skew')

    // A second run must not retry it: the error survives for the user to fix.
    await syncOnce()

    expect(pushRequests).toHaveLength(1)
    expect((await db.outbox.toArray())[0].status).toBe('rejected')
  })

  it('splits more than 100 pending changes into batches of 100', async () => {
    for (let index = 0; index < 150; index += 1) await saveLogbookEntry(draft({ yield_kg: index }))
    const { pushRequests } = stubSyncApi({
      push: (request) =>
        json({ results: request.changes.map((change) => ({ id: change.id, status: 'applied', server_version: 1 })) }),
    })

    const outcome = await syncOnce()

    expect(pushRequests).toHaveLength(2)
    expect(pushRequests[0].changes).toHaveLength(100)
    expect(pushRequests[1].changes).toHaveLength(50)
    expect(pushRequests[0].device_id).toBe(pushRequests[1].device_id)
    expect(outcome).toEqual({ status: 'synced', pushed: 150, pulled: 0 })
    expect(await db.outbox.count()).toBe(0)
  })

  it('does not settle a newer edit made while the push was in flight', async () => {
    const entry = draft({ yield_kg: 120 })
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: async (request) => {
        // The user edits again while this request is open: the answer is now
        // about a version no longer queued.
        await saveLogbookEntry({ ...entry, yield_kg: 150 })
        return json({
          results: request.changes.map((change) => ({ id: change.id, status: 'applied', server_version: 7 })),
        })
      },
    })

    await syncOnce()

    // The newer edit survives: still queued, and the row does not claim it.
    const items = await db.outbox.toArray()
    expect(items).toHaveLength(1)
    expect(items[0].data).toMatchObject({ yield_kg: 150 })
    expect(items[0].status).toBe('pending')
    const row = await db.logbookEntries.get(entry.id)
    expect(row?.syncState).toBe('pending')
    expect(row?.yield_kg).toBe(150)
  })

  it('does not flag a newer edit with a rejection either', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: async (request) => {
        await saveLogbookEntry({ ...entry, yield_kg: 150 })
        return json({ results: request.changes.map((c) => ({ id: c.id, status: 'rejected', server_version: null, error: 'clock_skew' })) })
      },
    })

    await syncOnce()

    // A rejection the server never saw must not stall the newer edit, which a
    // `rejected` item never re-pushes.
    const [item] = await db.outbox.toArray()
    expect(item.status).toBe('pending')
    expect(item.error).toBeNull()
    expect((await db.logbookEntries.get(entry.id))?.syncState).toBe('pending')
  })

  it('ignores a result for a change the run never sent', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    stubSyncApi({
      push: () => json({ results: [{ id: uuidv7(), status: 'applied', server_version: 1 }] }),
    })

    await syncOnce()

    // The real change is still queued: an answer about an id this run did not
    // push must not be taken as its result.
    expect(await db.outbox.count()).toBe(1)
    expect((await db.logbookEntries.get(entry.id))?.syncState).toBe('pending')
  })
})

describe('syncOnce: pull', () => {
  it('applies every page until has_more is false, advancing the cursor as it goes', async () => {
    const first = uuidv7()
    const second = uuidv7()
    const { pullQueries } = stubSyncApi({
      pull: (_since, index) =>
        index === 0
          ? json({ changes: [{ id: first, entity: 'logbook_entry', op: 'upsert', data: serverEntry(first), server_version: 5 }], next_since: 5, has_more: true })
          : json({ changes: [{ id: second, entity: 'logbook_entry', op: 'upsert', data: serverEntry(second), server_version: 9 }], next_since: 9, has_more: false }),
    })

    const outcome = await syncOnce()

    expect(pullQueries).toHaveLength(2)
    expect(pullQueries[0]).toContain('since=0')
    expect(pullQueries[1]).toContain('since=5')
    // The version comes from the change, not from the row payload.
    expect((await db.logbookEntries.get(first))?.server_version).toBe(5)
    expect((await db.logbookEntries.get(second))?.syncState).toBe('synced')
    expect(await getCursor()).toBe(9)
    expect(outcome).toEqual({ status: 'synced', pushed: 0, pulled: 2 })
  })

  it('does not overwrite a row that still has a change in the outbox, and does not claim it as applied', async () => {
    const entry = draft({ yield_kg: 120 })
    await saveLogbookEntry(entry)
    stubSyncApi({
      pull: () =>
        json({
          changes: [{ id: entry.id, entity: 'logbook_entry', op: 'upsert', data: serverEntry(entry.id), server_version: 8 }],
          next_since: 8,
          has_more: false,
        }),
    })

    const outcome = await syncOnce()

    // D7: the push decides this row, not the pull.
    const row = await db.logbookEntries.get(entry.id)
    expect(row?.yield_kg).toBe(120)
    expect(row?.server_version).toBeNull()
    // The change was pushed, but nothing was pulled in: the row was skipped.
    expect(outcome).toEqual({ status: 'synced', pushed: 1, pulled: 0 })
    // The cursor still moves: skipping the row must not rewind the device.
    expect(await getCursor()).toBe(8)
  })

  it('keeps a deleted row tombstoned when the server sends the delete', async () => {
    const gone = uuidv7()
    stubSyncApi({
      pull: () =>
        json({
          changes: [
            {
              id: gone,
              entity: 'logbook_entry',
              op: 'delete',
              data: serverEntry(gone, { deleted_at: '2026-09-28T12:00:00.000Z' }),
              server_version: 11,
            },
          ],
          next_since: 11,
          has_more: false,
        }),
    })

    await syncOnce()

    const row = await db.logbookEntries.get(gone)
    expect(row?.deleted_at).toBe('2026-09-28T12:00:00.000Z')
    expect(row?.syncState).toBe('synced')
    // A tombstoned row is not a live one: it can never be saved again (D13).
    await expect(saveLogbookEntry(draft({ id: gone }))).rejects.toThrow(/deleted/)
  })
})

describe('syncOnce: stopping without losing anything (D11)', () => {
  it('leaves the outbox and the cursor untouched on a 401, and signs the session out', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    const { pullQueries } = stubSyncApi({ push: () => new Response(null, { status: 401 }) })

    const outcome = await syncOnce()

    expect(outcome).toEqual({ status: 'stopped', reason: 'unauthorized' })
    expect(await db.outbox.count()).toBe(1)
    expect((await db.outbox.toArray())[0].status).toBe('pending')
    expect(await getCursor()).toBe(0)
    // No pull after a refused push: the run stops where it was.
    expect(pullQueries).toHaveLength(0)
    expect(navigations).toEqual(['/ingreso'])
  })

  it('reports a network failure as stopped, not as a run that synced nothing', async () => {
    const entry = draft()
    await saveLogbookEntry(entry)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    const outcome = await syncOnce()

    expect(outcome).toEqual({ status: 'stopped', reason: 'unavailable' })
    expect(await db.outbox.count()).toBe(1)
    expect(await getCursor()).toBe(0)
  })

  it('pushes nothing at all when there is no session', async () => {
    await saveLogbookEntry(draft())
    clearSession()
    const { pushRequests } = stubSyncApi({})

    const outcome = await syncOnce()

    expect(outcome).toEqual({ status: 'stopped', reason: 'unauthorized' })
    expect(pushRequests).toHaveLength(0)
  })
})

describe('syncOnce: single flight', () => {
  it('pushes once when two calls overlap, and gives both the same outcome', async () => {
    await saveLogbookEntry(draft())
    // The push is held open until the test releases it, so the second call
    // really does arrive while the first run is in flight.
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const { pushRequests } = stubSyncApi({
      push: async (request) => {
        await gate
        return json({ results: request.changes.map((change) => ({ id: change.id, status: 'applied', server_version: 1 })) })
      },
    })

    const first = syncOnce()
    const second = syncOnce()
    expect(second).toBe(first)
    release()
    const [a, b] = await Promise.all([first, second])

    expect(a).toEqual(b)
    expect(pushRequests).toHaveLength(1)
  })
})
