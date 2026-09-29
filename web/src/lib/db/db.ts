import Dexie, { type Table } from 'dexie'

/**
 * Local store for the offline logbook (ADR-0005: Dexie as the local primary
 * store; docs/07 §Flujo de datos y offline: the logbook is local-first).
 *
 * The row shapes are the docs/03 columns of `logbook_entry` and
 * `extension_visit`, plus two local fields the server never sees: `syncState`
 * and `syncError` (docs/06 §7 shows `conflict_overwritten` and `rejected` on
 * the entry, and a rejected change stays on the phone until the user fixes or
 * discards it). `server_version` is server-assigned and null until a push or
 * a pull brings it.
 */

export type SyncState = 'pending' | 'synced' | 'conflict_overwritten' | 'rejected'

/** docs/04 §Bitácora: the two entities a change can belong to. */
export type SyncEntity = 'logbook_entry' | 'extension_visit'

export type SyncOp = 'upsert' | 'delete'

/** `rejected` is terminal for an automatic run: only a new edit or a discard clears it. */
export type OutboxStatus = 'pending' | 'rejected'

export type LogbookKind = 'task' | 'input' | 'irrigation' | 'harvest' | 'observation' | 'cost'

export interface LogbookEntryRow {
  /** UUIDv7 generated on this device (docs/03), so an entry is born offline. */
  id: string
  org_id: string
  plot_id: string
  crop_cycle_id: string | null
  kind: LogbookKind
  occurred_on: string
  quantity: number | null
  unit: string | null
  cost_cop: number | null
  yield_kg: number | null
  sold_kg: number | null
  sale_price_cop_per_kg: number | null
  labor_days: number | null
  irrigation_mm: number | null
  alert_id: string | null
  notes: string | null
  created_by: string | null
  /** The client created it with no signal (docs/03). */
  created_offline: boolean
  client_updated_at: string
  server_version: number | null
  deleted_at: string | null
  syncState: SyncState
  syncError: string | null
}

export interface ExtensionVisitRow {
  id: string
  org_id: string
  farm_id: string
  plot_id: string | null
  technician_id: string
  visited_on: string
  /** The five topics of Ley 1876 (docs/03); the server rejects anything else. */
  topics: string[]
  recommendations: string | null
  commitments: string | null
  notes: string | null
  client_updated_at: string
  server_version: number | null
  deleted_at: string | null
  syncState: SyncState
  syncError: string | null
}

/**
 * The `data` a change carries on the wire (docs/04 §Bitácora): the docs/03
 * fields the client owns, with nothing server-assigned (`server_version`) and
 * no local tombstone (`deleted_at`, which the server sets itself).
 */
export type LogbookEntryData = Omit<
  LogbookEntryRow,
  'syncState' | 'syncError' | 'server_version' | 'deleted_at'
>

export type ExtensionVisitData = Omit<
  ExtensionVisitRow,
  'syncState' | 'syncError' | 'server_version' | 'deleted_at'
>

export interface OutboxItem {
  /** Auto-increment, so a batch is pushed in the order the changes were made. */
  key?: number
  id: string
  entity: SyncEntity
  op: SyncOp
  data: LogbookEntryData | ExtensionVisitData
  client_updated_at: string
  status: OutboxStatus
  /** One of docs/04 §Bitácora's `rejected` codes; null while pending. */
  error: string | null
}

/** What the store queues: `status` and `error` are the store's own bookkeeping,
 * so no caller supplies them. */
export type NewOutboxItem = Omit<OutboxItem, 'status' | 'error' | 'key'>

export type MetaKey = 'cursor' | 'deviceId'

export interface MetaRow {
  key: MetaKey
  value: string | number
}

export class TechcampDb extends Dexie {
  declare logbookEntries: Table<LogbookEntryRow, string>
  declare extensionVisits: Table<ExtensionVisitRow, string>
  declare outbox: Table<OutboxItem, number>
  declare meta: Table<MetaRow, string>

  constructor() {
    super('techcamp')
    // Indexes the store itself queries (`outbox.id` to replace the pending
    // change of an id, `outbox.status` to push) plus the listing indexes the
    // logbook and visits screens read (by plot, newest first); declaring them
    // now avoids a schema migration once those screens land.
    this.version(1).stores({
      logbookEntries: 'id, plot_id, occurred_on, syncState',
      extensionVisits: 'id, farm_id, visited_on, syncState',
      outbox: '++key, id, status',
      meta: 'key',
    })
  }
}

export const db = new TechcampDb()

let persistRequested = false

/**
 * Ask the browser once to keep this origin's IndexedDB out of the eviction
 * path (docs/06 §7 "Almacenamiento", ADR-0005: the browser can evict storage).
 * Guarded twice: older Safari and any non-secure context have no
 * `navigator.storage.persist`, and asking again would not change the answer.
 */
export function requestPersistentStorage(): void {
  if (persistRequested) return
  persistRequested = true
  const storage: StorageManager | undefined = navigator.storage
  if (storage === undefined || typeof storage.persist !== 'function') return
  void storage.persist()
}
