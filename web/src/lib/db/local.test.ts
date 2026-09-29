import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from './db'
import { deleteExtensionVisit, deleteLogbookEntry, saveExtensionVisit, saveLogbookEntry } from './local'
import type { ExtensionVisitDraft, LogbookEntryDraft } from './local'
import { resetLocalDb } from './testDb'
import { uuidv7 } from './ids'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000bb'
const FARM_ID = '018f0c2a-0000-7000-8000-0000000000cc'
const TECHNICIAN_ID = '018f0c2a-0000-7000-8000-0000000000dd'
const ALERT_ID = '018f0c2a-0000-7000-8000-0000000000ee'

function harvestDraft(overrides: Partial<LogbookEntryDraft> = {}): LogbookEntryDraft {
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
    sold_kg: 100,
    sale_price_cop_per_kg: 2500,
    labor_days: null,
    irrigation_mm: null,
    alert_id: null,
    notes: null,
    created_by: null,
    ...overrides,
  }
}

function visitDraft(overrides: Partial<ExtensionVisitDraft> = {}): ExtensionVisitDraft {
  return {
    id: uuidv7(),
    org_id: ORG_ID,
    farm_id: FARM_ID,
    plot_id: null,
    technician_id: TECHNICIAN_ID,
    visited_on: '2026-09-28',
    topics: ['manejo_de_suelos'],
    recommendations: null,
    commitments: null,
    notes: null,
    ...overrides,
  }
}

beforeEach(async () => {
  await resetLocalDb()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('saveLogbookEntry', () => {
  it('stores the row and its pending outbox change in one transaction, marked as written offline', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    const draft = harvestDraft({ notes: 'cosecha de prueba', alert_id: ALERT_ID })
    expect(await db.outbox.count()).toBe(0)

    const saved = await saveLogbookEntry(draft)

    expect(saved.syncState).toBe('pending')
    expect(saved.syncError).toBeNull()
    expect(saved.created_offline).toBe(true)
    expect(saved.client_updated_at).toMatch(/^\d{4}-\d{2}-\d{2}T.*Z$/)
    const [item] = await db.outbox.toArray()
    expect(item.id).toBe(draft.id)
    expect(item.entity).toBe('logbook_entry')
    expect(item.op).toBe('upsert')
    expect(item.status).toBe('pending')
    expect(item.client_updated_at).toBe(saved.client_updated_at)
    // The payload is the docs/03 fields the client owns: no sync bookkeeping,
    // no server-assigned version, no local tombstone.
    expect(item.data).toMatchObject({ kind: 'harvest', yield_kg: 120, sold_kg: 100, notes: 'cosecha de prueba', alert_id: ALERT_ID })
    expect(item.data).not.toHaveProperty('syncState')
    expect(item.data).not.toHaveProperty('server_version')
    expect(item.data).not.toHaveProperty('deleted_at')
    // A row that did not exist before is not reported as an edit: `created_offline`
    // follows the connection, and an edit keeps the value the create recorded.
    await saveLogbookEntry({ ...draft, notes: 'editada en línea' })
    const edited = await db.logbookEntries.get(draft.id)
    expect(edited?.created_offline).toBe(true)
  })
})

describe('saveLogbookEntry (re-edit)', () => {
  it('replaces the pending change of the same id instead of queueing a second one', async () => {
    const draft = harvestDraft()
    await saveLogbookEntry(draft)
    await saveLogbookEntry({ ...draft, yield_kg: 150 })

    const items = await db.outbox.toArray()
    expect(items).toHaveLength(1)
    expect(items[0].data).toMatchObject({ yield_kg: 150 })
    expect(await db.logbookEntries.count()).toBe(1)
    const row = await db.logbookEntries.get(draft.id)
    expect(row?.yield_kg).toBe(150)
  })
})

describe('deleteLogbookEntry', () => {
  it('soft-deletes the row and pushes a delete change, and an unknown id changes nothing', async () => {
    const draft = harvestDraft()
    await saveLogbookEntry(draft)
    await db.outbox.clear()
    const before = await db.logbookEntries.get(draft.id)
    expect(before?.deleted_at).toBeNull()

    const deleted = await deleteLogbookEntry(draft.id)

    expect(deleted.deleted_at).not.toBeNull()
    expect(deleted.syncState).toBe('pending')
    const [item] = await db.outbox.toArray()
    expect(item.op).toBe('delete')
    expect(item.entity).toBe('logbook_entry')
    expect(item.id).toBe(draft.id)
    // A delete of an id this phone never stored is not a tombstone: there is
    // nothing to propagate, so it is refused instead of queued.
    await expect(deleteLogbookEntry(uuidv7())).rejects.toThrow(/logbook entry/)
    expect(await db.outbox.count()).toBe(1)
    expect(await db.logbookEntries.get(draft.id)).toMatchObject({ deleted_at: deleted.deleted_at })
  })
})

describe('extension visits', () => {
  it('stores a visit and its outbox change as an extension_visit, and soft-deletes it the same way', async () => {
    const draft = visitDraft({ notes: 'visita de prueba' })
    expect(await db.outbox.count()).toBe(0)

    const saved = await saveExtensionVisit(draft)

    expect(saved.syncState).toBe('pending')
    expect(saved.topics).toEqual(['manejo_de_suelos'])
    const [item] = await db.outbox.toArray()
    expect(item.entity).toBe('extension_visit')
    expect(item.op).toBe('upsert')
    expect(item.data).toMatchObject({ farm_id: FARM_ID, technician_id: TECHNICIAN_ID, notes: 'visita de prueba' })
    // The other entity's table is untouched by a visit write.
    expect(await db.logbookEntries.count()).toBe(0)
    expect(item.data).not.toHaveProperty('syncState')

    const deleted = await deleteExtensionVisit(draft.id)

    expect(deleted.deleted_at).not.toBeNull()
    const after = await db.outbox.toArray()
    expect(after).toHaveLength(1)
    expect(after[0].op).toBe('delete')
    expect(after[0].entity).toBe('extension_visit')
    await expect(deleteExtensionVisit(uuidv7())).rejects.toThrow(/extension visit/)
    expect(await db.outbox.count()).toBe(1)
  })
})
