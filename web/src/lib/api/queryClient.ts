import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister'
import type { PersistQueryClientOptions } from '@tanstack/react-query-persist-client'
import { QueryClient } from '@tanstack/react-query'

/**
 * `retry: false`: this app's own retry UX is the explicit "Reintentar"
 * button (`refetch()`), not TanStack Query's automatic background retries —
 * two competing retry mechanisms would make failure timing non-deterministic
 * for both the user and the tests.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
})

/** docs/07 §Flujo de datos y offline: "la caché persistida vence a los 7 días". */
const CACHE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

/**
 * Busts the whole persisted cache when the app changes shape under it — a new
 * version of `/status` or `/me/tray`, a renamed column the cache serializes.
 * A persisted cache from an older build would otherwise be restored as if it
 * were current, and the user would read a field that no longer means what the
 * screen says it means.
 *
 * A hand-written revision, because this app has no build stamp to read at
 * runtime (`web/package.json` is `0.0.0` and nothing injects a version into
 * the bundle): bump it in the same work unit as the change that invalidates
 * the cache, or that build keeps showing the old shape.
 */
const CACHE_BUSTER = '1'

/** The one row this app's persister owns. */
export const PERSIST_CACHE_KEY = 'techcamp.query-cache'

/**
 * Dexie behind the persister's `AsyncStorage` interface (`getItem`/`setItem`/
 * `removeItem` — the v5 persister's whole contract).
 *
 * `db` is imported lazily, and every failure is absorbed: Dexie is the
 * heaviest dependency this app keeps out of the initial bundle (RNF-02), and a
 * phone with storage disabled or full must still get a working app, just one
 * whose cache does not survive a reload.
 */
const dexieStorage = {
  getItem: async (key: string): Promise<string | null> => {
    try {
      const { db } = await import('../db/db')
      const row = await db.queryCache.get(key)
      return row?.value ?? null
    } catch {
      return null
    }
  },
  setItem: async (key: string, value: string): Promise<void> => {
    try {
      const { db } = await import('../db/db')
      await db.queryCache.put({ key, value })
    } catch {
      /* a cache that cannot be written is a cache that is not persisted */
    }
  },
  removeItem: async (key: string): Promise<void> => {
    try {
      const { db } = await import('../db/db')
      await db.queryCache.delete(key)
    } catch {
      /* see setItem */
    }
  },
}

const cachePersister = createAsyncStoragePersister({
  storage: dexieStorage,
  key: PERSIST_CACHE_KEY,
})

/**
 * How the app's query cache is persisted; handed as-is to
 * `PersistQueryClientProvider` (`App.tsx`).
 *
 * ## The convention (T5/T6 read this before writing a persisted query)
 *
 * Persistence is OPT-IN: a query is written to disk only when it declares
 * `meta: { persist: true }`. Everything else — the auth seed above all — stays
 * in memory, so nothing reaches the phone's storage by accident.
 *
 * A persisted query key MUST start with the org id, as
 * `[orgId, 'status']` or `[orgId, 'tray', technicianId]`. docs/07 says so
 * ("las claves de consulta llevan la organización") and it is what keeps the
 * cache honest across org switches: the key is the only thing separating one
 * organization's data from another's on one phone, and `clearSession` wipes
 * the cache only on sign-out, not on an org change.
 *
 * Mutations are never persisted: this app has no offline mutation queue for
 * them (the outbox in `db.ts` is the logbook's), and a paused mutation on disk
 * is a write the UI never promised to retry.
 */
export const persistOptions: Omit<PersistQueryClientOptions, 'queryClient'> = {
  persister: cachePersister,
  maxAge: CACHE_MAX_AGE_MS,
  buster: CACHE_BUSTER,
  dehydrateOptions: {
    shouldDehydrateQuery: (query) => query.meta?.persist === true,
    shouldDehydrateMutation: () => false,
  },
}

/**
 * Drops the persisted cache from the phone. `clearSession()` calls it on every
 * sign-out (and every expired-token 401): a shared phone must not hand the
 * previous user's farms to the next one, so the cache goes with the token
 * rather than living until its 7 days are up.
 */
export async function clearPersistedCache(): Promise<void> {
  await dexieStorage.removeItem(PERSIST_CACHE_KEY)
}
