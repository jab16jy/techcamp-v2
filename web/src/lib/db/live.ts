import { liveQuery } from 'dexie'
import { useSyncExternalStore } from 'react'
import { db } from './db'

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
