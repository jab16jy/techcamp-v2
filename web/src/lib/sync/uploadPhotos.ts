import { db } from '../db/db'
import type { PhotoRow } from '../db/db'
import { photoBlob } from '../db/photos'
import { presignPhoto, putPhoto, type PresignPhotoRequest } from './transport'

/**
 * The photo half of one synchronization run: `POST /attachments:presign` then a
 * PUT to object storage, after the push and the pull (docs/06 §7 ends every run
 * with "fotos pendientes: presign → PUT a S3").
 *
 * A photo is only uploaded once its parent is on the server — D8 answers `404`
 * while the entry or visit is not visible yet, and presigning early would burn
 * an `attachment` row (and leave an orphan object) for a parent that may still
 * be edited on this phone.
 */

/**
 * Drains the pending photos of this device.
 *
 * Every failure is per photo: one photo the server refuses, or a PUT that does
 * not land, must not stop the next photo nor end the run. The one exception is
 * the expired session, which `presignPhoto` throws so the run stops with the
 * outbox and the photos untouched (D11).
 */
export async function uploadPendingPhotos(): Promise<void> {
  const pending = await db.photos.where('status').equals('pending').toArray()
  for (const photo of pending) {
    await uploadPhoto(photo)
  }
}

async function uploadPhoto(photo: PhotoRow): Promise<void> {
  const parent = await loadParent(photo)
  if (parent === 'deleted') {
    // D13: a delete is final and there is no undelete, so a photo of a deleted
    // entry or visit has no parent to hang from. This is the only photo the
    // client drops by itself, and it only drops what has no parent left.
    await db.photos.delete(photo.id)
    return
  }
  if (parent === 'not_synced') return
  // A local edit the server has not seen yet (D7: the push decides the row).
  // Presigning now would attach the photo to a version the user is changing.
  const queued = await db.outbox
    .where('id')
    .equals(photo.parent_id)
    .filter((item) => item.status === 'pending')
    .count()
  if (queued > 0) return

  const outcome = await presignPhoto(presignRequest(photo))
  if (outcome.status === 'parent_not_synced' || outcome.status === 'not_sent') return
  if (outcome.status === 'rejected') {
    // docs/04 §Bitácora "Fotos": a 422 is final for this photo, and its reason
    // is what the user is shown (a third state, never a silent "false").
    await db.photos.update(photo.id, { status: 'failed', error: outcome.reason })
    return
  }

  const body = photoBlob(photo)
  if (body === null) return
  const landed = await putPhoto(outcome.uploadUrl, body, photo.content_type)
  if (!landed) return
  await db.photos.update(photo.id, { status: 'uploaded', error: null, data: null })
}

function presignRequest(photo: PhotoRow): PresignPhotoRequest {
  const parentId =
    photo.entity === 'logbook_entry'
      ? { logbook_entry_id: photo.parent_id }
      : { extension_visit_id: photo.parent_id }
  return { ...parentId, content_type: photo.content_type, bytes: photo.bytes }
}

/**
 * Whether the photo's parent is on the server (`ready`), is still being written
 * on this device (`not_synced`), or is gone (`deleted`).
 */
async function loadParent(photo: PhotoRow): Promise<'ready' | 'not_synced' | 'deleted'> {
  const row =
    photo.entity === 'logbook_entry'
      ? await db.logbookEntries.get(photo.parent_id)
      : await db.extensionVisits.get(photo.parent_id)
  // A photo is only ever stored against a parent this device saved, so a parent
  // that is missing was deleted and its tombstone reaped.
  if (row === undefined || row.deleted_at !== null) return 'deleted'
  if (row.server_version === null) return 'not_synced'
  return 'ready'
}
