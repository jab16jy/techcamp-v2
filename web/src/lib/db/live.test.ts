import 'fake-indexeddb/auto'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { usePhotos } from './live'
import { savePhoto } from './photos'
import { resetLocalDb } from './testDb'

const PARENT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

beforeEach(async () => {
  await resetLocalDb()
})

describe('usePhotos', () => {
  it('drops the photos of a previous parent the moment there is no parent', async () => {
    await savePhoto({
      id: '0192f0c2a-0000-7000-8000-0000000000a1',
      entity: 'logbook_entry',
      parent_id: PARENT_ID,
      data: new Uint8Array(new ArrayBuffer(900)),
      content_type: 'image/jpeg',
      bytes: 900,
    })

    const { result, rerender } = renderHook(
      ({ parentId }: { parentId: string | null }) => usePhotos('logbook_entry', parentId),
      { initialProps: { parentId: PARENT_ID as string | null } },
    )
    await waitFor(() => expect(result.current).toHaveLength(1))

    rerender({ parentId: null })

    // Negative: an editor that moved on to a record with no id yet must not
    // keep showing — or be able to remove — the photos of the one it left.
    expect(result.current).toEqual([])
  })
})
