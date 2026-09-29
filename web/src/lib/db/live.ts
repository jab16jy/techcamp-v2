import { liveQuery } from 'dexie'
import { useCallback, useMemo, useRef, useSyncExternalStore } from 'react'
import { db } from './db'
import type { PhotoRow, SyncEntity } from './db'
import { listPhotos } from './photos'

/**
 * The number of changes still waiting to be pushed, live. This is the
 * `pendingCount` the existing `SyncIndicator` prints (docs/07), kept here
 * because the outbox is the only place that knows.
 *
 * Dexie's `liveQuery` re-runs the count after every write that could change it,
 * and the React binding is the same `useSyncExternalStore` shape as
 * `lib/api/session.ts` rather than the separate `dexie-react-hooks` package:
 * one number does not justify another dependency in the bundle.
 *
 * The query is only observed while someone is listening, so importing this
 * module does not open an IndexedDB connection by itself.
 */
const pendingCountQuery = liveQuery(() => db.outbox.where('status').equals('pending').count())

type Listener = () => void

const listeners = new Set<Listener>()
let currentCount = 0
let subscription: { unsubscribe: () => void } | null = null

export function getPendingCount(): number {
  return currentCount
}

export function subscribePendingCount(listener: Listener): () => void {
  listeners.add(listener)
  subscription ??= pendingCountQuery.subscribe({
    next: (count) => {
      currentCount = count
      for (const pending of listeners) pending()
    },
  })
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      subscription?.unsubscribe()
      subscription = null
    }
  }
}

/** `pendingCount` for a component. 0 on the server render, which never happens here. */
export function usePendingCount(): number {
  return useSyncExternalStore(subscribePendingCount, getPendingCount, () => 0)
}

const NO_PHOTOS: PhotoRow[] = []

/**
 * The photos of one entry or visit, live (ADR-0018, E8 T9b).
 *
 * `parentId` is null while the sheet is filling a record that does not exist
 * yet: its photos are staged in the component and stored once the parent has an
 * id, and there is nothing to observe from the store until then.
 *
 * Read through `useSyncExternalStore` rather than state-in-an-effect, because
 * the store is exactly what that hook is for: the subscription is Dexie's
 * `liveQuery`, so a photo that the upload queue marks `uploaded` flips its
 * thumbnail's state while the sheet is open, and the snapshot the hook reads is
 * the last value the query published — never a copy this component has to
 * re-synchronise. A failing read keeps the last known list on screen instead of
 * emptying it: the user still sees the photos they took.
 *
 * With no parent there is nothing to observe, and the snapshot answers that
 * rather than the previous parent's rows: `rowsRef` still holds them, so a
 * consumer reading it directly (or removing by id) would act on a record it has
 * already left.
 */
export function usePhotos(entity: SyncEntity, parentId: string | null): PhotoRow[] {
  const query = useMemo(
    () => (parentId === null ? null : liveQuery(() => listPhotos(entity, parentId))),
    [entity, parentId],
  )
  const rowsRef = useRef<PhotoRow[]>(NO_PHOTOS)

  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (query === null) return () => {}
      const subscription = query.subscribe({
        next: (rows) => {
          rowsRef.current = rows
          onStoreChange()
        },
      })
      return () => subscription.unsubscribe()
    },
    [query],
  )
  const getSnapshot = useCallback(() => (query === null ? NO_PHOTOS : rowsRef.current), [query])

  return useSyncExternalStore(subscribe, getSnapshot, () => NO_PHOTOS)
}
