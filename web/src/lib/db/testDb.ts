import Dexie from 'dexie'
import { IDBFactory } from 'fake-indexeddb'
import { db } from './db'

/**
 * A fresh IndexedDB per test. `fake-indexeddb/auto` installs one shared
 * factory for the whole process, so without this a test would see the rows
 * another test wrote. `Dexie.dependencies.indexedDB` is the same seam
 * `fake-indexeddb/auto` itself patches on Dexie.
 *
 * `db.delete()` comes first and is not optional: after a transaction that
 * threw, `db.close()` plus a new factory silently hands back the old database
 * (the failed connection is still cached), so a test that exercises a
 * rejected transaction would leak its rows into the next one.
 *
 * Test-only support for `local.test.ts` and the synchronizer's tests; nothing
 * in the app imports it, so it never reaches the bundle.
 */
export async function resetLocalDb(): Promise<void> {
  await db.delete()
  Dexie.dependencies.indexedDB = new IDBFactory()
  await db.open()
}
