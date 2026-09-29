import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setSession } from '../api/session'
import { db } from '../db/db'
import { uuidv7 } from '../db/ids'
import {
  saveExtensionVisit,
  saveLogbookEntry,
  type ExtensionVisitDraft,
  type LogbookEntryDraft,
} from '../db/local'
import { resetLocalDb } from '../db/testDb'
import { syncOnce } from './synchronizer'
import type { PushRequest, PushResult } from './types'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000bb'
const FARM_ID = '018f0c2a-0000-7000-8000-0000000000cc'
const TECHNICIAN_ID = '018f0c2a-0000-7000-8000-0000000000dd'

function entryDraft(overrides: Partial<LogbookEntryDraft> = {}): LogbookEntryDraft {
  return {
    id: uuidv7(),
    org_id: ORG_ID,
    plot_id: PLOT_ID,
    crop_cycle_id: null,
    kind: 'harvest',
    occurred_on: '2026-09-29',
    quantity: null,
    unit: null,
    cost_cop: null,
    yield_kg: 250.5,
    sold_kg: null,
    sale_price_cop_per_kg: null,
    labor_days: null,
    irrigation_mm: null,
    alert_id: null,
    notes: 'cosecha en modo avión',
    created_by: null,
    ...overrides,
  }
}

function visitDraft(overrides: Partial<ExtensionVisitDraft> = {}): ExtensionVisitDraft {
  return {
    id: uuidv7(),
    org_id: ORG_ID,
    farm_id: FARM_ID,
    plot_id: PLOT_ID,
    technician_id: TECHNICIAN_ID,
    visited_on: '2026-09-29',
    topics: ['natural_resources'],
    recommendations: 'rotar el cultivo',
    commitments: null,
    notes: 'visita sin señal',
    ...overrides,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface FakeRow {
  entity: 'logbook_entry' | 'extension_visit'
  client_updated_at: string
  version: number
  data: Record<string, unknown>
}

/**
 * A stateful server behind the transport, so scenario D is proven end to end
 * on the client side too (docs/06 §7). It decides like docs/04 §Bitácora: the
 * same id with the same `client_updated_at` answers `duplicate` and never
 * touches the stored row, so replaying a batch cannot add a second row
 * (ADR-0013). While `navigator.onLine` is false the request never leaves the
 * phone: the fetch throws, exactly like a browser with no network.
 *
 * It is a plain fetch double, the same seam the synchronizer tests already
 * stub, so it lives behind the real transport (`pushChanges`/`pullChanges`)
 * and the real `apiClient`, not in front of the store.
 */
function startFakeServer(): {
  pushRequests: PushRequest[]
  rowCount: () => number
  row: (id: string) => FakeRow | undefined
  loseNextResponse: () => void
} {
  const rows = new Map<string, FakeRow>()
  const pushRequests: PushRequest[] = []
  let lastVersion = 0
  let loseNextPushResponse = false

  const handle = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (!navigator.onLine) throw new TypeError('Failed to fetch')

    const url = input instanceof Request ? input.url : String(input)
    if (url.endsWith('/push')) {
      const raw = input instanceof Request ? await input.clone().text() : String(init?.body)
      const request = JSON.parse(raw) as PushRequest
      pushRequests.push(request)
      const results: PushResult[] = request.changes.map((change) => {
        const stored = rows.get(change.id)
        if (stored !== undefined && stored.client_updated_at === change.client_updated_at) {
          return {
            id: change.id,
            status: 'duplicate',
            server_version: stored.version,
            error: null,
          }
        }
        lastVersion += 1
        rows.set(change.id, {
          entity: change.entity,
          client_updated_at: change.client_updated_at,
          version: lastVersion,
          data: { ...(change.data as Record<string, unknown>), deleted_at: null },
        })
        return { id: change.id, status: 'applied', server_version: lastVersion, error: null }
      })
      if (loseNextPushResponse) {
        // The change is applied and committed; the answer dies on the way
        // back, which is the one failure the phone cannot tell apart from
        // "never arrived". The next run must therefore replay the very same
        // change and hear `duplicate` (ADR-0013).
        loseNextPushResponse = false
        throw new TypeError('Failed to fetch')
      }
      return json({ results })
    }

    const since = Number(new URL(url).searchParams.get('since'))
    return json({
      changes: [...rows.entries()]
        .filter(([, row]) => row.version > since)
        .sort(([, a], [, b]) => a.version - b.version)
        .map(([id, row]) => ({
          id,
          entity: row.entity,
          op: 'upsert',
          data: row.data,
          server_version: row.version,
        })),
      next_since: lastVersion,
      has_more: false,
    })
  }

  vi.stubGlobal('fetch', vi.fn(handle))
  return {
    pushRequests,
    rowCount: () => rows.size,
    row: (id: string) => rows.get(id),
    loseNextResponse: () => {
      loseNextPushResponse = true
    },
  }
}

function setOnline(online: boolean): void {
  Object.defineProperty(window.navigator, 'onLine', { value: online, configurable: true })
}

beforeEach(async () => {
  await resetLocalDb()
  setSession('test-token', null)
  setOnline(true)
})

afterEach(() => {
  delete (window.navigator as unknown as { onLine?: boolean }).onLine
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('scenario D: offline harvest', () => {
  it('stays on the phone while offline, then syncs once and never duplicates', async () => {
    const entry = entryDraft()
    setOnline(false)

    // The logbook sheet's own store call, in airplane mode.
    await saveLogbookEntry(entry)

    const stored = await db.logbookEntries.get(entry.id)
    expect(stored).toMatchObject({ created_offline: true, syncState: 'pending', server_version: null })
    expect(await db.outbox.count()).toBe(1)

    const server = startFakeServer()

    // A run with no signal: nothing reaches the server and nothing is lost.
    const offlineOutcome = await syncOnce()
    expect(offlineOutcome).toEqual({ status: 'stopped', reason: 'unavailable' })
    expect(server.pushRequests).toHaveLength(0)
    expect(await db.outbox.count()).toBe(1)
    expect((await db.outbox.toArray())[0].status).toBe('pending')

    // Back online, but the answer is lost after the server applied the change:
    // the phone must keep the change queued and must not claim a version.
    setOnline(true)
    server.loseNextResponse()
    const lostOutcome = await syncOnce()

    expect(lostOutcome).toEqual({ status: 'stopped', reason: 'unavailable' })
    expect(server.pushRequests).toHaveLength(1)
    expect(server.row(entry.id)?.data.created_offline).toBe(true)
    expect(server.rowCount()).toBe(1)
    expect(await db.outbox.count()).toBe(1)
    expect((await db.outbox.toArray())[0].status).toBe('pending')
    expect(await db.logbookEntries.get(entry.id)).toMatchObject({
      syncState: 'pending',
      server_version: null,
    })

    // The next run replays that very same change: the server answers
    // `duplicate` (same id, same client_updated_at) and keeps one row, and
    // the phone settles it with the version the server already had (ADR-0013).
    const onlineOutcome = await syncOnce()

    expect(onlineOutcome).toEqual({ status: 'synced', pushed: 1, pulled: 1 })
    expect(server.pushRequests).toHaveLength(2)
    expect(server.pushRequests[1].changes.map((change) => change.id)).toEqual([entry.id])
    expect(server.pushRequests[1].changes[0].client_updated_at).toBe(
      server.pushRequests[0].changes[0].client_updated_at,
    )
    expect(server.rowCount()).toBe(1)
    expect(server.row(entry.id)?.version).toBe(1)
    expect(await db.outbox.count()).toBe(0)
    expect(await db.logbookEntries.get(entry.id)).toMatchObject({
      syncState: 'synced',
      server_version: 1,
    })

    // A further run has nothing queued and adds nothing.
    await syncOnce()

    expect(server.pushRequests).toHaveLength(2)
    expect(server.rowCount()).toBe(1)
  })
})

describe('scenario D: offline extension visit', () => {
  it('follows the same store, outbox and duplicate rules as the logbook', async () => {
    const visit = visitDraft()
    setOnline(false)

    // The visit sheet's own store call, in airplane mode.
    await saveExtensionVisit(visit)

    expect(await db.extensionVisits.get(visit.id)).toMatchObject({ syncState: 'pending' })
    expect(await db.outbox.count()).toBe(1)

    const server = startFakeServer()

    const offlineOutcome = await syncOnce()
    expect(offlineOutcome).toEqual({ status: 'stopped', reason: 'unavailable' })
    expect(server.pushRequests).toHaveLength(0)
    expect(await db.outbox.count()).toBe(1)

    setOnline(true)
    server.loseNextResponse()
    const lostOutcome = await syncOnce()

    expect(lostOutcome).toEqual({ status: 'stopped', reason: 'unavailable' })
    expect(server.pushRequests).toHaveLength(1)
    expect(server.pushRequests[0].changes[0].entity).toBe('extension_visit')
    expect(server.rowCount()).toBe(1)
    expect(await db.outbox.count()).toBe(1)

    const onlineOutcome = await syncOnce()

    expect(onlineOutcome).toEqual({ status: 'synced', pushed: 1, pulled: 1 })
    expect(server.pushRequests).toHaveLength(2)
    expect(server.pushRequests[1].changes[0].entity).toBe('extension_visit')
    expect(server.pushRequests[1].changes[0].client_updated_at).toBe(
      server.pushRequests[0].changes[0].client_updated_at,
    )
    expect(server.rowCount()).toBe(1)
    expect(server.row(visit.id)?.version).toBe(1)
    expect(await db.outbox.count()).toBe(0)
    expect(await db.extensionVisits.get(visit.id)).toMatchObject({
      syncState: 'synced',
      server_version: 1,
      topics: ['natural_resources'],
      technician_id: TECHNICIAN_ID,
    })

    await syncOnce()
    expect(server.pushRequests).toHaveLength(2)
    expect(server.rowCount()).toBe(1)
  })
})
