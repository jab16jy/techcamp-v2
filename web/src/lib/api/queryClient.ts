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
const CACHE_BUSTER = '3'

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
/**
 * What one `queryCache` row holds: the persister's opaque JSON bound to the
 * session that wrote it. A row is the previous session's data the moment the
 * next user signs in, so the session it was written under is stored beside it
 * and checked on every read.
 */
interface PersistedCacheRow {
  /** Hex SHA-256 of the session token — never the token itself. */
  session: string
  /** The persister's own JSON: its envelope and dehydrated state, opaque here. */
  cache: string
}

/**
 * Hex SHA-256 of a session token.
 *
 * Binding a row to its session is a comparison, and a comparison never needs the
 * token back — only something derived from it. Storing the token itself would
 * put a second long-lived copy of a bearer credential in IndexedDB, which is
 * precisely what the sign-out wipe exists to stop: the phone must not keep the
 * previous user's secrets. A digest authorizes exactly its own row and nothing
 * else, and one that leaked is not a credential anyone can present.
 *
 * `crypto.subtle` needs a secure context, which a PWA already requires to
 * install and run; where it is missing the digest rejects, the adapter's `catch`
 * absorbs it, and the cache simply does not persist.
 */
async function tokenDigest(token: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** Anything this store did not write — an older format, a torn write — is no cache. */
function parseRow(raw: string): PersistedCacheRow | null {
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedCacheRow>
    if (typeof parsed?.session !== 'string' || typeof parsed?.cache !== 'string') return null
    return { session: parsed.session, cache: parsed.cache }
  } catch {
    return null
  }
}

const dexieStorage = {
  /**
   * The single read path: `createAsyncStoragePersister`'s `restoreClient` calls
   * this and nothing else reads the row, so gating here gates every restore.
   *
   * It fails closed twice, because the cache outlives the session that wrote
   * it. `clearSession()` deletes the row on a promise nobody awaits, so a row
   * can survive its session — the OS killing a PWA on a shared phone is the
   * normal way that happens — and two things must then be true:
   *
   * - No token, no restore: the row is erased and nothing comes back (#204).
   * - The right token, and only the right token: a row written by a different
   *   session is erased rather than handed over. This is the shared-phone case
   *   #204 left open — a new sign-in on the same device would otherwise have
   *   authorized the previous user's cache. Org-scoped keys do not close it:
   *   a technician and a producer sharing one phone are in the SAME org, so the
   *   keys match and the previous user's plot status renders for the new one.
   *
   * Both erasures are the one the killed process skipped, retried on the next
   * start. This app issues the token once per sign-in (no refresh; an expired
   * token ends the session), so a different token is always a different user.
   */
  getItem: async (key: string): Promise<string | null> => {
    try {
      const [{ db }, { getToken }] = await Promise.all([
        import('../db/db'),
        import('./session'),
      ])
      const token = getToken()
      if (token === null) {
        await db.queryCache.delete(key)
        return null
      }
      const row = await db.queryCache.get(key)
      if (row === undefined) return null
      const parsed = parseRow(row.value)
      if (parsed === null || parsed.session !== (await tokenDigest(token))) {
        await db.queryCache.delete(key)
        return null
      }
      return parsed.cache
    } catch {
      return null
    }
  },
  setItem: async (key: string, value: string): Promise<void> => {
    try {
      const [{ db }, { getToken }] = await Promise.all([
        import('../db/db'),
        import('./session'),
      ])
      const token = getToken()
      // Nothing to restore for a session that does not exist, and a row no one
      // could read is a row nobody should write.
      if (token === null) return
      const row: PersistedCacheRow = { session: await tokenDigest(token), cache: value }
      await db.queryCache.put({ key, value: JSON.stringify(row) })
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
