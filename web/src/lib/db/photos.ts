import { db } from './db'
import type { NewPhoto, PhotoRow, SyncEntity } from './db'
import { publishLocalWrite } from './local'

/**
 * The photo half of the local store (ADR-0005: Dexie is the local primary
 * store; ADR-0018: the client holds the bytes and PUTs them straight to
 * object storage, so nothing here talks to the API).
 *
 * A photo is written before its parent has been synced — it is born offline
 * like the entry it belongs to — and the synchronizer's upload queue drains it
 * later (docs/06 §7: "fotos pendientes: presign → PUT a S3").
 */

/**
 * Stores a photo on this device, pending its upload, and tells the
 * synchronizer there is something new to send.
 */
export async function savePhoto(photo: NewPhoto): Promise<PhotoRow> {
  const row: PhotoRow = {
    ...photo,
    status: 'pending',
    error: null,
    created_at: new Date().toISOString(),
  }
  await db.photos.put(row)
  // Outside the write, like every other local write: the synchronizer runs
  // 2 s later (docs/06 §7).
  publishLocalWrite()
  return row
}

/** The photos of one entry or visit, oldest first (the order they were taken). */
export async function listPhotos(entity: SyncEntity, parentId: string): Promise<PhotoRow[]> {
  const photos = await db.photos.where('[entity+parent_id]').equals([entity, parentId]).toArray()
  return photos.sort((left, right) => left.created_at.localeCompare(right.created_at))
}

/**
 * The Blob a stored photo describes, built from its own bytes.
 *
 * One construction for both consumers: the upload queue PUTs exactly this Blob
 * (its `size` is what it also told presign, and what the presigned URL signs as
 * `Content-Length`), and the sheet renders its thumbnail from the same one.
 * `null` once the photo is `uploaded`, because the bytes were dropped then.
 */
export function photoBlob(photo: PhotoRow): Blob | null {
  if (photo.data === null) return null
  return new Blob([photo.data], { type: photo.content_type })
}

/**
 * Removes a photo this device stored. Removing one that is not there is not an
 * error: the sheet lets the user remove a photo before it is saved, and a
 * second tap on an already-removed thumbnail must not throw.
 */
export async function removePhoto(id: string): Promise<void> {
  await db.photos.delete(id)
}
