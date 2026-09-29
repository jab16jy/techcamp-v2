import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it } from 'vitest'
import { db } from '../db/db'
import { listPhotos } from '../db/photos'
import { resetLocalDb } from '../db/testDb'
import { PHOTO_MAX_BYTES, compressPhotoWithinLimit, encodedPhoto } from './compress'
import { addPhoto } from './addPhoto'

const PARENT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

beforeEach(async () => {
  await resetLocalDb()
})

describe('adding a photo to an entry', () => {
  it('stores the compressed photo against its parent, pending and unsent', async () => {
    const stored = await addPhoto(
      { file: new Blob(['hola']), entity: 'logbook_entry', parentId: PARENT_ID },
      async () => encodedPhoto(new Uint8Array([1, 2, 3, 4]), 'image/jpeg'),
    )

    expect(stored.parent_id).toBe(PARENT_ID)
    expect(stored.status).toBe('pending')
    expect(stored.bytes).toBe(4)
    // The bytes presign will sign and the PUT will send are the same bytes.
    expect(stored.data?.byteLength).toBe(4)
    expect(await db.outbox.count()).toBe(0)
    expect(await listPhotos('logbook_entry', PARENT_ID)).toHaveLength(1)
  })
})

describe('a photo that cannot be compressed under 200 KB', () => {
  it('is refused and nothing is stored for it', async () => {
    await expect(
      addPhoto(
        { file: new Blob(['hola']), entity: 'logbook_entry', parentId: PARENT_ID },
        // The real ladder, with an encoder that never gets under the ceiling.
        () => compressPhotoWithinLimit(async () => encodedPhoto(new Uint8Array(PHOTO_MAX_BYTES + 1), 'image/jpeg')),
      ),
    ).rejects.toThrow('200 KB')

    expect(await db.photos.count()).toBe(0)
  })
})
