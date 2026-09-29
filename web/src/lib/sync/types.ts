import type { ExtensionVisitRow, LogbookEntryRow, OutboxItem, SyncEntity, SyncOp } from '../db/db'

/**
 * The sync wire shapes, transcribed from docs/04 §Bitácora.
 *
 * Hand-written on purpose: `/api/v1/sync/push` and `/api/v1/sync/pull` are not
 * in `schema.d.ts` yet (T3 and T4 add them). T8 regenerates the schema and swaps
 * `transport.ts` for the typed `apiClient`, at which point this file goes away.
 */

/** One queued change, exactly as it goes on the wire. */
export interface PushChange {
  id: string
  entity: SyncEntity
  op: SyncOp
  data: OutboxItem['data']
  client_updated_at: string
}

export interface PushRequest {
  device_id: string
  changes: PushChange[]
}

export type PushStatus = 'applied' | 'duplicate' | 'conflict_overwritten' | 'rejected'

export interface PushResult {
  id: string
  status: PushStatus
  /** Absent for `rejected`; for `conflict_overwritten` it is the winner's. */
  server_version: number | null
  /** One of docs/04 §Bitácora's stable codes; only ever set with `rejected`. */
  error?: string | null
}

export interface PushResponse {
  results: PushResult[]
}

interface PullChangeBase {
  id: string
  op: SyncOp
  /** The cursor value this change occupies; the row's own version is not trusted. */
  server_version: number
}

/**
 * A discriminated union rather than one shape with a `ServerRow` payload, so
 * narrowing on `entity` also narrows `data`: the synchronizer can then build
 * each row without a cast, and a field the server forgot on one entity fails
 * the build here instead of landing in the store as `undefined`.
 */
export type PullChange =
  | (PullChangeBase & {
      entity: 'logbook_entry'
      data: Omit<LogbookEntryRow, 'syncState' | 'syncError' | 'server_version'>
    })
  | (PullChangeBase & {
      entity: 'extension_visit'
      data: Omit<ExtensionVisitRow, 'syncState' | 'syncError' | 'server_version'>
    })

export interface PullResponse {
  changes: PullChange[]
  next_since: number
  has_more: boolean
}
