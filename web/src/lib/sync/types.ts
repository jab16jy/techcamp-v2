import type { components } from '../api/schema'
import type { ExtensionVisitRow, LogbookEntryRow, SyncOp } from '../db/db'

/**
 * The sync wire shapes, derived from `schema.d.ts` (docs/04 §Bitácora).
 */

export type PushRequest = components['schemas']['PushRequest']
export type PushResponse = components['schemas']['PushResponse']
export type PushResult = components['schemas']['PushResultView']
export type PushStatus = 'applied' | 'duplicate' | 'conflict_overwritten' | 'rejected'
export type PushChange = PushRequest['changes'][number]

interface PullChangeBase {
  id: string
  op: SyncOp
  /** The cursor value this change occupies; the row's own version is not trusted. */
  server_version: number
}

/**
 * A discriminated union so narrowing on `entity` also narrows `data`: the
 * synchronizer can build each row without a cast, failing closed on unknown entities.
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
