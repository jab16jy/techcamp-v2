import type { Table } from 'dexie'
import { db } from './db'
import type {
  ExtensionVisitData,
  ExtensionVisitRow,
  LogbookEntryData,
  LogbookEntryRow,
  NewOutboxItem,
  SyncEntity,
} from './db'

/**
 * What a screen hands the store: the docs/03 fields the client owns. The
 * fields the store or the server owns are absent on purpose, so a caller
 * cannot invent a `server_version`, a tombstone or a sync state.
 */
export type LogbookEntryDraft = Omit<
  LogbookEntryRow,
  'created_offline' | 'client_updated_at' | 'server_version' | 'deleted_at' | 'syncState' | 'syncError'
>

export type ExtensionVisitDraft = Omit<
  ExtensionVisitRow,
  'client_updated_at' | 'server_version' | 'deleted_at' | 'syncState' | 'syncError'
>

/** ISO 8601 in UTC, the wire format of `client_updated_at` (docs/03). */
function nowIso(): string {
  return new Date().toISOString()
}

/**
 * The `data` a change carries (docs/04 §Bitácora): the docs/03 fields the
 * client owns, and nothing else. The four fields this device or the server own
 * (`syncState`, `syncError`, `server_version`, `deleted_at`) are left out by
 * listing the rest, not by casting the row to an `Omit<...>` type: a cast
 * would typecheck while still sending the local bookkeeping to the server.
 */
function toEntryData(row: LogbookEntryRow): LogbookEntryData {
  return {
    id: row.id,
    org_id: row.org_id,
    plot_id: row.plot_id,
    crop_cycle_id: row.crop_cycle_id,
    kind: row.kind,
    occurred_on: row.occurred_on,
    quantity: row.quantity,
    unit: row.unit,
    cost_cop: row.cost_cop,
    yield_kg: row.yield_kg,
    sold_kg: row.sold_kg,
    sale_price_cop_per_kg: row.sale_price_cop_per_kg,
    labor_days: row.labor_days,
    irrigation_mm: row.irrigation_mm,
    alert_id: row.alert_id,
    notes: row.notes,
    created_by: row.created_by,
    created_offline: row.created_offline,
    client_updated_at: row.client_updated_at,
  }
}

function toVisitData(row: ExtensionVisitRow): ExtensionVisitData {
  return {
    id: row.id,
    org_id: row.org_id,
    farm_id: row.farm_id,
    plot_id: row.plot_id,
    technician_id: row.technician_id,
    visited_on: row.visited_on,
    topics: row.topics,
    recommendations: row.recommendations,
    commitments: row.commitments,
    notes: row.notes,
    client_updated_at: row.client_updated_at,
  }
}

/**
 * One pending change per id: the previous outbox item for this id is dropped
 * and the new one takes its place, in the same transaction as the row. A
 * re-edit therefore replaces the earlier attempt instead of queueing a second
 * push of the same row, and an edit that fixes a rejection clears the rejected
 * item with its error (docs/06 §7: a rejected change is only dropped by a new
 * edit or by an explicit discard, never silently).
 */
async function replaceOutboxItem(item: NewOutboxItem): Promise<void> {
  await db.outbox.where('id').equals(item.id).delete()
  await db.outbox.add({ ...item, status: 'pending', error: null })
}

/**
 * A delete is final (D13: docs/07 has no undelete), so a save refuses a row
 * this device already tombstoned instead of quietly writing an upsert over the
 * tombstone. Raising here rather than in the screen makes the undelete
 * unrepresentable: the store cannot end up with a live local row whose delete
 * the server already applied.
 */
function assertNotDeleted(
  stored: LogbookEntryRow | ExtensionVisitRow | undefined,
  entity: string,
  id: string,
): void {
  if (stored?.deleted_at != null) {
    throw new Error(`${entity} ${id} was deleted on this device; a delete is final`)
  }
}

/**
 * Creates or updates a logbook entry on this device and queues its push.
 *
 * The row and its outbox item are written in ONE transaction, so the invariant
 * that nothing written offline is dropped silently holds across a crash or a
 * failed write: a stored entry with no queued change (or the reverse) is not a
 * state this store can produce.
 */
export async function saveLogbookEntry(draft: LogbookEntryDraft): Promise<LogbookEntryRow> {
  const at = nowIso()
  return db.transaction('rw', [db.logbookEntries, db.outbox], async () => {
    // Read inside the transaction, never from an earlier call: `created_offline`
    // and `server_version` must reflect the stored row right now.
    const stored = await db.logbookEntries.get(draft.id)
    assertNotDeleted(stored, 'logbook entry', draft.id)
    const row: LogbookEntryRow = {
      ...draft,
      // Only the creation sees the connection; an edit keeps what the create
      // recorded, so the flag still means "this phone wrote it with no signal".
      created_offline: stored === undefined ? !navigator.onLine : stored.created_offline,
      client_updated_at: at,
      server_version: stored?.server_version ?? null,
      // Unreachable as anything but null: a deleted row cannot be saved (D13).
      deleted_at: null,
      syncState: 'pending',
      syncError: null,
    }
    await db.logbookEntries.put(row)
    await replaceOutboxItem({
      id: row.id,
      entity: 'logbook_entry',
      op: 'upsert',
      data: toEntryData(row),
      client_updated_at: at,
    })
    return row
  })
}

/**
 * Soft-deletes a logbook entry (docs/03: logical delete, so the tombstone
 * reaches the other devices) and queues the delete change.
 */
export async function deleteLogbookEntry(id: string): Promise<LogbookEntryRow> {
  return deleteLocally(id, 'logbook_entry', db.logbookEntries, toEntryData)
}

/** Creates or updates an extension visit and queues its push (docs/03 `extension_visit`). */
export async function saveExtensionVisit(draft: ExtensionVisitDraft): Promise<ExtensionVisitRow> {
  const at = nowIso()
  return db.transaction('rw', [db.extensionVisits, db.outbox], async () => {
    const stored = await db.extensionVisits.get(draft.id)
    assertNotDeleted(stored, 'extension visit', draft.id)
    const row: ExtensionVisitRow = {
      ...draft,
      client_updated_at: at,
      server_version: stored?.server_version ?? null,
      // Unreachable as anything but null: a deleted row cannot be saved (D13).
      deleted_at: null,
      syncState: 'pending',
      syncError: null,
    }
    await db.extensionVisits.put(row)
    await replaceOutboxItem({
      id: row.id,
      entity: 'extension_visit',
      op: 'upsert',
      data: toVisitData(row),
      client_updated_at: at,
    })
    return row
  })
}

/** Soft-deletes an extension visit and queues the delete change. */
export async function deleteExtensionVisit(id: string): Promise<ExtensionVisitRow> {
  return deleteLocally(id, 'extension_visit', db.extensionVisits, toVisitData)
}

async function deleteLocally<TRow extends LogbookEntryRow | ExtensionVisitRow>(
  id: string,
  entity: SyncEntity,
  table: Table<TRow, string>,
  toData: (row: TRow) => LogbookEntryData | ExtensionVisitData,
): Promise<TRow> {
  const at = nowIso()
  return db.transaction('rw', [table, db.outbox], async () => {
    const stored = await table.get(id)
    // An id this phone never stored has nothing to tombstone. Refusing keeps a
    // stray call from queueing a change for a row that does not exist, instead
    // of inventing a row to carry it.
    if (stored === undefined) {
      throw new Error(`no ${entity.replace('_', ' ')} ${id} to delete on this device`)
    }
    const row = {
      ...stored,
      deleted_at: at,
      client_updated_at: at,
      syncState: 'pending',
      syncError: null,
    } as TRow
    await table.put(row)
    await replaceOutboxItem({
      id,
      entity,
      op: 'delete',
      data: toData(row),
      client_updated_at: at,
    })
    return row
  })
}
