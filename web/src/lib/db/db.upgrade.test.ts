import 'fake-indexeddb/auto'
import Dexie from 'dexie'
import { beforeEach, describe, expect, it } from 'vitest'
import { db } from './db'
import type { ExtensionVisitRow, LogbookEntryRow, OutboxItem } from './db'

/**
 * The v1 → v2 upgrade (photos, E8 T9b), on its own file and its own
 * `beforeEach` on purpose: `resetLocalDb` points `Dexie.dependencies` at a
 * fresh `IDBFactory`, while the `db` singleton keeps the factory it was
 * constructed with. A legacy instance created after that would write a
 * DIFFERENT physical database that happens to share the name, and the upgrade
 * would appear to lose every row. So this file never calls `resetLocalDb`: it
 * wipes the one database `db` opens, and the legacy instance below is built
 * with the same dependency `db` has.
 */

const ENTRY_ID = '018f0c2a-0000-7000-8000-0000000000bb'
const VISIT_ID = '018f0c2a-0000-7000-8000-0000000000cc'
const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'

/** Exactly what `version(1)` declared before the photos table existed. */
const VERSION_1_STORES = {
  logbookEntries: 'id, plot_id, occurred_on, syncState',
  extensionVisits: 'id, farm_id, visited_on, syncState',
  outbox: '++key, id, status',
  meta: 'key',
}

function entryRow(): LogbookEntryRow {
  return {
    id: ENTRY_ID,
    org_id: ORG_ID,
    plot_id: '018f0c2a-0000-7000-8000-0000000000dd',
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
    created_offline: true,
    client_updated_at: '2026-09-28T10:00:00.000Z',
    server_version: 7,
    deleted_at: null,
    syncState: 'synced',
    syncError: null,
  }
}

function visitRow(): ExtensionVisitRow {
  return {
    id: VISIT_ID,
    org_id: ORG_ID,
    farm_id: '018f0c2a-0000-7000-8000-0000000000ee',
    plot_id: null,
    technician_id: '018f0c2a-0000-7000-8000-0000000000ff',
    visited_on: '2026-09-28',
    topics: ['agua'],
    recommendations: null,
    commitments: null,
    notes: null,
    client_updated_at: '2026-09-28T11:00:00.000Z',
    server_version: 8,
    deleted_at: null,
    syncState: 'synced',
    syncError: null,
  }
}

function outboxItem(): Omit<OutboxItem, 'key'> {
  return {
    id: ENTRY_ID,
    entity: 'logbook_entry',
    op: 'upsert',
    data: entryRow(),
    client_updated_at: '2026-09-28T10:00:00.000Z',
    status: 'pending',
    error: null,
  }
}

beforeEach(async () => {
  await db.delete()
  await db.close()
})

describe('opening a phone that only has version 1 data', () => {
  it('keeps every entry, visit and outbox item and adds the photos table', async () => {
    const legacy = new Dexie('techcamp')
    legacy.version(1).stores(VERSION_1_STORES)
    await legacy.open()
    await legacy.table('logbookEntries').put(entryRow())
    await legacy.table('extensionVisits').put(visitRow())
    await legacy.table('outbox').add(outboxItem())
    await legacy.close()

    await db.open()

    expect(await db.logbookEntries.get(ENTRY_ID)).toMatchObject({ yield_kg: 120 })
    expect(await db.extensionVisits.get(VISIT_ID)).toMatchObject({ topics: ['agua'] })
    expect(await db.outbox.where('id').equals(ENTRY_ID).count()).toBe(1)
    // The upgrade only ADDS a table: a phone that never took a photo has none.
    expect(await db.photos.where('status').equals('pending').count()).toBe(0)
  })
})
