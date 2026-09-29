import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'
import { db } from './db'
import type { PhotoBytes } from './db'
import { listPhotos, removePhoto, savePhoto } from './photos'
import { resetLocalDb } from './testDb'

const ENTRY_ID = '018f0c2a-0000-7000-8000-0000000000bb'
const VISIT_ID = '018f0c2a-0000-7000-8000-0000000000cc'

/** The compressed photo the store holds: three bytes of a JPEG. */
function photoBytes(): PhotoBytes {
  return new Uint8Array([1, 2, 3])
}

beforeEach(async () => {
  await resetLocalDb()
})

describe('saving a photo', () => {
  it('stores the bytes and never queues a push of its own', async () => {
    const row = await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a1',
      entity: 'logbook_entry',
      parent_id: ENTRY_ID,
      data: photoBytes(),
      content_type: 'image/jpeg',
      bytes: 3,
    })

    expect(row.status).toBe('pending')
    expect(row.error).toBeNull()
    const stored = await db.photos.get(row.id)
    // The bytes survive IndexedDB intact, because the upload PUTs exactly
    // them: their length is what presign signs as `Content-Length`.
    expect([...(stored?.data ?? [])]).toEqual([1, 2, 3])
    expect(stored?.bytes).toBe(3)
    // ADR-0018: the API never receives photo bytes, so a photo is not a change
    // in the outbox — only the parent entry is pushed.
    expect(await db.outbox.count()).toBe(0)
  })
})

describe('listing the photos of a parent', () => {
  it('returns that parent\'s photos and leaves another parent\'s alone', async () => {
    await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a1',
      entity: 'logbook_entry',
      parent_id: ENTRY_ID,
      data: photoBytes(),
      content_type: 'image/jpeg',
      bytes: 3,
    })
    await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a2',
      entity: 'extension_visit',
      parent_id: VISIT_ID,
      data: photoBytes(),
      content_type: 'image/jpeg',
      bytes: 3,
    })

    const listed = await listPhotos('logbook_entry', ENTRY_ID)

    expect(listed.map((photo) => photo.parent_id)).toEqual([ENTRY_ID])
  })
})

describe('removing a photo before it is saved', () => {
  it('drops only that photo, and an id it never stored is not an error', async () => {
    const kept = await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a1',
      entity: 'logbook_entry',
      parent_id: ENTRY_ID,
      data: photoBytes(),
      content_type: 'image/jpeg',
      bytes: 3,
    })
    await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a2',
      entity: 'logbook_entry',
      parent_id: ENTRY_ID,
      data: photoBytes(),
      content_type: 'image/jpeg',
      bytes: 3,
    })

    await removePhoto(kept.id)
    await removePhoto('0192f0c2a-0000-7000-8000-0000000000ff')

    const left = await listPhotos('logbook_entry', ENTRY_ID)
    expect(left.map((photo) => photo.id)).toEqual(['0192f0c2a-0000-7000-8000-0000000000a2'])
  })
})
