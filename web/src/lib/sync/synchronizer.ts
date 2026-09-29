import type { Table } from 'dexie'
import { db } from '../db/db'
import type { ExtensionVisitData, ExtensionVisitRow, LogbookEntryData, LogbookEntryRow, OutboxItem, SyncEntity } from '../db/db'
import { getCursor, getDeviceId, setCursor } from '../db/meta'
import { recordSyncOutcome } from './syncState'
import { pullChanges, pushChanges, type SyncStopReason, SyncStoppedError } from './transport'
import { uploadPendingPhotos } from './uploadPhotos'
import type { PullResponse, PushChange, PushResult } from './types'

/** docs/04 §Bitácora: a batch carries at most 100 changes, and the server answers `422` beyond that. */
const PUSH_BATCH_SIZE = 100

/** docs/04 §Bitácora: `GET /sync/pull?since=&limit=500`. */
const PULL_LIMIT = 500

export type SyncOutcome =
  | { status: 'synced'; pushed: number; pulled: number }
  | { status: 'stopped'; reason: SyncStopReason }

let running: Promise<SyncOutcome> | null = null

/**
 * One synchronization run: push what this device owes, then pull what it is
 * missing. Single-flight, because every trigger (open, `online`, the interval,
 * a local write) can fire while a run is in flight and the outbox is shared
 * state — a second concurrent run would push the same change twice and race the
 * cursor. A call made during a run gets that run's promise, not a new one.
 */
export function syncOnce(): Promise<SyncOutcome> {
  running ??= run().finally(() => {
    running = null
  })
  return running
}

async function run(): Promise<SyncOutcome> {
  try {
    const pushed = await pushPending()
    const pulled = await pullMissing()
    // docs/06 §7: the run ends with the pending photos. After the push, so an
    // entry created offline is already on the server (D8 asks for a synced
    // parent), and after the pull, so a parent another device synced is
    // visible before its photos are presigned against it.
    await uploadPendingPhotos()
    const outcome: SyncOutcome = { status: 'synced', pushed, pulled }
    recordSyncOutcome(outcome)
    return outcome
  } catch (error) {
    // A stopped run is a distinct outcome, never a silent success: the caller
    // has to be able to tell "nothing to do" from "we could not ask" (D11).
    if (error instanceof SyncStoppedError) {
      const outcome: SyncOutcome = { status: 'stopped', reason: error.reason }
      recordSyncOutcome(outcome)
      return outcome
    }
    throw error
  }
}

async function pushPending(): Promise<number> {
  // Only `pending` items are ever selected, which is what makes a `rejected`
  // change stay put instead of retrying forever.
  const pending = await db.outbox.where('status').equals('pending').sortBy('key')
  if (pending.length === 0) return 0

  const deviceId = await getDeviceId()
  let pushed = 0
  // A snapshot of the pending set, not a re-query per batch: a batch the server
  // did not answer for must not be re-selected into an endless loop, and a
  // change written during this run belongs to the next one.
  for (let index = 0; index < pending.length; index += PUSH_BATCH_SIZE) {
    const batch = pending.slice(index, index + PUSH_BATCH_SIZE)
    const response = await pushChanges({ device_id: deviceId, changes: batch.map(toPushChange) })
    await applyResults(batch, response.results)
    pushed += batch.length
  }
  return pushed
}

function toPushChange(item: OutboxItem): PushChange {
  if (item.entity === 'logbook_entry') {
    return {
      id: item.id,
      entity: 'logbook_entry',
      op: item.op,
      data: item.data as LogbookEntryData,
      client_updated_at: item.client_updated_at,
    }
  }
  return {
    id: item.id,
    entity: 'extension_visit',
    op: item.op,
    data: item.data as ExtensionVisitData,
    client_updated_at: item.client_updated_at,
  }
}

async function applyResults(batch: OutboxItem[], results: PushResult[]): Promise<void> {
  for (const result of results) {
    const item = batch.find((candidate) => candidate.id === result.id)
    // The server answered about a change this run never sent. There is nothing
    // local to settle, and inventing one would be worse than ignoring it.
    if (item === undefined) continue
    if (result.status === 'rejected') {
      if (item.entity === 'logbook_entry') {
        await markRejected(db.logbookEntries, item, result.error ?? null)
      } else {
        await markRejected(db.extensionVisits, item, result.error ?? null)
      }
      continue
    }
    if (item.entity === 'logbook_entry') {
      await settle(db.logbookEntries, item, result)
    } else {
      await settle(db.extensionVisits, item, result)
    }
  }
}

/**
 * Whether the queued change is still the one the server answered about. An edit
 * made while the request was in flight REPLACES the outbox item, and settling the
 * newer one would drop an unsynced write (docs/06 §7, RNF-01). The key is the
 * identity, not `client_updated_at`: two edits in one millisecond share one.
 */
async function isStillQueued(item: OutboxItem): Promise<boolean> {
  // No key means the change cannot be proven identical, so it stays queued and
  // is re-pushed: the server answers that with `duplicate` (ADR-0013).
  const current = await db.outbox.where('id').equals(item.id).first()
  return current !== undefined && current.key === item.key
}

/**
 * A rejected change keeps its place and its error (docs/06 §7: it stays on the
 * phone until the user fixes or discards it, never silently dropped). Only a new
 * edit, which replaces the item, or an explicit discard clears it.
 */
async function markRejected<TRow extends LogbookEntryRow | ExtensionVisitRow>(
  table: Table<TRow, string>,
  item: OutboxItem,
  error: string | null,
): Promise<void> {
  await db.transaction('rw', [table, db.outbox], async () => {
    if (!(await isStillQueued(item))) return
    await db.outbox.where('id').equals(item.id).modify({ status: 'rejected', error })
    const row = await table.get(item.id)
    if (row === undefined) return
    await table.put({ ...row, syncState: 'rejected', syncError: error })
  })
}

async function settle<TRow extends LogbookEntryRow | ExtensionVisitRow>(
  table: Table<TRow, string>,
  item: OutboxItem,
  result: PushResult,
): Promise<void> {
  await db.transaction('rw', [table, db.outbox], async () => {
    // A newer edit under this id is not what the server answered about.
    if (!(await isStillQueued(item))) return
    // Every non-rejected outcome hands the change over to the server, so the
    // queued item goes away — including a conflict, whose winner is not ours.
    await db.outbox.where('id').equals(item.id).delete()
    const row = await table.get(item.id)
    if (row === undefined) return
    if (result.status === 'conflict_overwritten') {
      // Another device's newer `client_updated_at` won. This device keeps its
      // own content, flagged for the user, and the winner lands on the pull that
      // follows: the row has no queued change left, so D7 lets it through.
      await table.put({ ...row, syncState: 'conflict_overwritten' })
      return
    }
    await table.put({
      ...row,
      syncState: 'synced',
      syncError: null,
      server_version: result.server_version ?? row.server_version,
    })
  })
}

async function pullMissing(): Promise<number> {
  let pulled = 0
  for (;;) {
    const since = await getCursor()
    const page = await pullChanges(since, PULL_LIMIT)
    pulled += await applyPage(page)
    if (!page.has_more) return pulled
  }
}

/**
 * Applies one page and advances the cursor in the SAME transaction: a crash
 * between the two would leave the cursor past changes this device never
 * applied, and those changes would never come back (the cursor is monotonic).
 */
async function applyPage(page: PullResponse): Promise<number> {
  return db.transaction(
    'rw',
    [db.logbookEntries, db.extensionVisits, db.outbox, db.meta],
    async () => {
      let applied = 0
      for (const change of page.changes) {
        // The entity is DATA, not a type: the discriminated union is a promise
        // about the wire, and an entity this client does not sync would fall
        // into the `else` below and be written into the wrong table. Fail the
        // page closed instead. The throw aborts this transaction, so the cursor
        // does not advance and no row from this page is half-applied: a skipped
        // change is a silent loss, and a cursor past it never brings it back.
        if (!isKnownEntity(change.entity)) {
          throw new SyncStoppedError('unknown_entity', null)
        }
        // D7 (docs/06 §7 "Pull de un registro con cambio local pendiente"): a row
        // with a change in the outbox is never overwritten by a pull; the push
        // decides it. ANY queued change blocks, including a rejected one, since
        // overwriting that would drop the user's unsynced edit and its error
        // without a word.
        const queued = await db.outbox.where('id').equals(change.id).count()
        if (queued > 0) continue
        // The server's row becomes the local row, with `server_version` taken
        // from the change itself (docs/04 §Bitácora carries it there) and the two
        // local sync fields derived here, never taken from the wire. A `delete`
        // keeps the `deleted_at` the server sent, so the row reads as tombstoned
        // instead of coming back to life.
        if (change.entity === 'logbook_entry') {
          await db.logbookEntries.put({
            ...change.data,
            server_version: change.server_version,
            syncState: 'synced',
            syncError: null,
          })
        } else {
          await db.extensionVisits.put({
            ...change.data,
            server_version: change.server_version,
            syncState: 'synced',
            syncError: null,
          })
        }
        applied += 1
      }
      await setCursor(page.next_since)
      return applied
    },
  )
}

/** The two entities this client syncs (docs/04 §Bitácora). */
function isKnownEntity(entity: unknown): entity is SyncEntity {
  return entity === 'logbook_entry' || entity === 'extension_visit'
}
