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

/** ADR-0018: the photo went up or did not; `failed` carries why. */
export type PhotoStatus = 'pending' | 'uploaded' | 'failed'

/**
 * The compressed bytes of a photo, in a plain buffer.
 *
 * The parameter is what lets `new Blob([data])` typecheck under strict mode
 * (TypeScript 5.7 types `Uint8Array` over `ArrayBufferLike`, and a
 * `BlobPart` is an `ArrayBufferView<ArrayBuffer>`), and it is exactly what
 * IndexedDB gives back: a copy in the database's own buffer.
 */
export type PhotoBytes = Uint8Array<ArrayBuffer>

/**
 * One photo waiting to reach object storage, stored on the phone only.
 *
 * It is never a row in the outbox: ADR-0018 keeps the bytes out of the API
 * (the client PUTs them straight to the presigned URL), so only the parent
 * entry or visit is pushed. `bytes` is what `POST /attachments:presign` is
 * told and what the presigned URL signs as `Content-Length`, so the upload
 * must PUT exactly those bytes (`bytes === data.byteLength`, always).
 *
 * `data` holds the compressed bytes rather than a `Blob`: that is what
 * IndexedDB stores a Blob as anyway (the HTML structured clone serializes it
 * to bytes + type), it is the one shape both a browser and `fake-indexeddb`
 * clone natively (a jsdom `Blob` does not survive Node's `structuredClone`),
 * and the upload builds the Blob it PUTs from these very bytes, once.
 *
 * `data` is null once the photo is `uploaded`: the object is in storage and
 * the local copy only keeps phone storage (ADR-0018's storage note).
 */
export interface PhotoRow {
  id: string
  entity: SyncEntity
  parent_id: string
  data: PhotoBytes | null
  content_type: string
  bytes: number
  status: PhotoStatus
  /** Why a `failed` photo failed, shown to the user; null otherwise. */
  error: string | null
  created_at: string
}

/** What the store queues: `status`, `error` and `created_at` are the store's. */
export type NewPhoto = Pick<
  PhotoRow,
  'id' | 'entity' | 'parent_id' | 'data' | 'content_type' | 'bytes'
>

/**
 * One serialized TanStack Query cache (docs/07 §Flujo de datos y offline: the
 * cache is persisted in this same IndexedDB, `q -- persistencia de caché -->
 * dx`).
 *
 * `value` is the persister's own JSON, one opaque string per `key` rather than
 * a row per query: the persister owns the envelope (its `timestamp`, `buster`
 * and dehydrated state) and rewrites it whole on every save, so this store
 * never has to know a query's shape — and a query key is not a primary key
 * here, which is also why the offline logbook tables stay untouched by a cache
 * that is wiped on sign-out.
 */
export interface QueryCacheRow {
  key: string
  value: string
}

export class TechcampDb extends Dexie {
  declare logbookEntries: Table<LogbookEntryRow, string>
  declare extensionVisits: Table<ExtensionVisitRow, string>
  declare outbox: Table<OutboxItem, number>
  declare meta: Table<MetaRow, string>
  declare photos: Table<PhotoRow, string>
  declare queryCache: Table<QueryCacheRow, string>

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
    // Photos (ADR-0018, E8 T9b). Dexie adds the table and keeps every v1 row:
    // an upgrade that dropped a phone's unsynced logbook would break RNF-01.
    // Only `photos` is declared, Dexie's own way: a version states what it
    // CHANGES, and repeating the v1 lines would make a later edit to one of
    // them silently alter (and empty) an existing table instead of applying
    // to the next version. `status` is what the upload queue selects;
    // `[entity+parent_id]` is the one lookup the two sheets make, answered by
    // an index instead of a scan of every photo on the phone.
    this.version(2).stores({
      photos: 'id, [entity+parent_id], status',
    })
    // The persisted query cache (docs/07 §Flujo de datos y offline, E9 T4), the
    // same "declare only what this version changes" rule: a phone's unsynced
    // logbook and outbox must survive it (see `db.upgrade.test.ts`).
    this.version(3).stores({
      queryCache: 'key',
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
  // A refusal is not a failure the app can act on, and this function hands the
  // caller no promise to handle, so the rejection is absorbed here rather than
  // surfacing as a global unhandled rejection from a best-effort call (#144).
  void storage.persist().catch(() => {})
}
