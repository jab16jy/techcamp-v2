import { db } from './db'
import { uuidv7 } from './ids'

/**
 * The pull cursor and this device's id, the two values that are not a row.
 * Both live in the `meta` table (docs/06 §7: one cursor covers both synced
 * tables, because they share the `server_version` sequence).
 */

/** The last `server_version` this device applied. 0 means "nothing yet". */
export async function getCursor(): Promise<number> {
  const row = await db.meta.get('cursor')
  return row === undefined ? 0 : Number(row.value)
}

export async function setCursor(nextSince: number): Promise<void> {
  await db.meta.put({ key: 'cursor', value: nextSince })
}

/**
 * Stamps every change this device pushes (docs/04 §Bitácora). Generated once
 * and kept: a new id per run would make the server treat one device as many,
 * and `uuidv7` keeps it sortable and offline-generatable.
 */
export async function getDeviceId(): Promise<string> {
  const row = await db.meta.get('deviceId')
  if (row !== undefined) return String(row.value)
  const deviceId = uuidv7()
  await db.meta.put({ key: 'deviceId', value: deviceId })
  return deviceId
}
