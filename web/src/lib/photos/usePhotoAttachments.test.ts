import 'fake-indexeddb/auto'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { db } from '../db/db'
import { savePhoto } from '../db/photos'
import { resetLocalDb } from '../db/testDb'
import { PHOTO_TOO_LARGE_MESSAGE, PhotoTooLargeError, encodedPhoto } from './compress'
import { usePhotoAttachments } from './usePhotoAttachments'

const PARENT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

/** A compressor that stands in for the canvas one: jsdom has no canvas. */
const compressing = async () => encodedPhoto(new Uint8Array(new ArrayBuffer(1200)), 'image/jpeg')

function fileList(...names: string[]): FileList {
  const files = names.map((name) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/jpeg' }))
  return {
    length: files.length,
    item: (index: number) => files[index] ?? null,
    ...Object.fromEntries(files.map((file, index) => [index, file])),
    [Symbol.iterator]: function* () {
      yield* files
    },
  } as unknown as FileList
}

beforeEach(async () => {
  await resetLocalDb()
})

describe('a photo chosen before the entry is saved', () => {
  it('is staged with a preview, stored only when the parent exists, then cleared', async () => {
    const { result } = renderHook(() => usePhotoAttachments('logbook_entry', null, compressing))

    await act(async () => {
      await result.current.addFiles(fileList('cosecha.jpg'))
    })

    expect(result.current.items).toHaveLength(1)
    expect(result.current.items[0].status).toBe('pending')
    expect(result.current.items[0].previewUrl).toBeTruthy()
    // Negative: a photo of a record that does not exist yet is nowhere in the
    // store — nothing half-attached is left behind.
    expect(await db.photos.count()).toBe(0)

    await act(async () => {
      await result.current.attachTo(PARENT_ID)
    })

    const stored = await db.photos.toArray()
    expect(stored).toHaveLength(1)
    expect(stored[0].parent_id).toBe(PARENT_ID)
    expect(stored[0].entity).toBe('logbook_entry')
    expect(stored[0].status).toBe('pending')
    expect(stored[0].bytes).toBe(1200)
    expect(result.current.items).toEqual([])
    expect(result.current.error).toBeNull()
  })
})

describe('a photo that will not fit under 200 KB', () => {
  it('is refused with the message the sheet shows, and nothing is staged', async () => {
    const refusing = async () => {
      throw new PhotoTooLargeError(PHOTO_TOO_LARGE_MESSAGE)
    }
    const { result } = renderHook(() => usePhotoAttachments('logbook_entry', null, refusing))

    await act(async () => {
      await result.current.addFiles(fileList('pesada.jpg'))
    })

    expect(result.current.error).toBe(PHOTO_TOO_LARGE_MESSAGE)
    expect(result.current.items).toEqual([])
    expect(await db.photos.count()).toBe(0)
  })
})

describe('removing a photo before it is saved', () => {
  it('drops it from the sheet and stores nothing', async () => {
    const { result } = renderHook(() => usePhotoAttachments('logbook_entry', null, compressing))
    await act(async () => {
      await result.current.addFiles(fileList('a.jpg'))
    })

    await act(async () => {
      await result.current.remove(result.current.items[0].id)
    })

    expect(result.current.items).toEqual([])
    expect(await db.photos.count()).toBe(0)
  })
})

describe('a photo already stored on this device', () => {
  it('is listed with its state, and removing it deletes the row', async () => {
    await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a1',
      entity: 'logbook_entry',
      parent_id: PARENT_ID,
      data: new Uint8Array(new ArrayBuffer(900)),
      content_type: 'image/jpeg',
      bytes: 900,
    })
    await db.photos.update('0192f0c2a-0000-7000-8000-0000000000a1', {
      status: 'failed',
      error: 'bytes must be <= 204800',
    })

    const { result } = renderHook(() => usePhotoAttachments('logbook_entry', PARENT_ID, compressing))

    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.items[0].status).toBe('failed')
    expect(result.current.items[0].error).toBe('bytes must be <= 204800')

    await act(async () => {
      await result.current.remove('0192f0c2a-0000-7000-8000-0000000000a1')
    })

    await waitFor(async () => expect(await db.photos.count()).toBe(0))
  })
})

describe('a photo of a visit', () => {
  it('is attached as an extension_visit photo, not a logbook one', async () => {
    const { result } = renderHook(() => usePhotoAttachments('extension_visit', null, compressing))
    await act(async () => {
      await result.current.addFiles(fileList('visita.jpg'))
    })

    await act(async () => {
      await result.current.attachTo(PARENT_ID)
    })

    const stored = await db.photos.toArray()
    expect(stored[0].entity).toBe('extension_visit')
  })
})
