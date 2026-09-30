import 'fake-indexeddb/auto'
import {
  persistQueryClientRestore,
  persistQueryClientSave,
} from '@tanstack/react-query-persist-client'
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { db } from '../db/db'
import { resetLocalDb } from '../db/testDb'
import { PERSIST_CACHE_KEY, persistOptions, queryClient } from './queryClient'
import { clearSession } from './session'

/**
 * The persisted cache of docs/07 §Flujo de datos y offline ("TanStack Query
 * con caché persistida en IndexedDB (Dexie) ... las claves de consulta llevan
 * la organización y la caché persistida vence a los 7 días").
 *
 * Every test writes through `persistQueryClientSave` and reads back through
 * `persistQueryClientRestore` with the SAME `persistOptions` the app hands to
 * `PersistQueryClientProvider`, so the max age, the buster and the opt-in
 * filter under test are the shipped ones. `fake-indexeddb/auto` is the E8
 * arrangement: one shared factory, reset per test by `resetLocalDb`.
 *
 * Real timers, deliberately: the async persister throttles its writes by 1 s
 * (`throttleTime`), and faking timers would fake IndexedDB's own scheduling
 * with them.
 */

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
/** The org id leads the key (the convention `queryClient.ts` documents). */
const STATUS_KEY = [ORG_ID, 'status']
const ME_KEY = ['me', 'token-abc']
const STATUS = { plot_id: '018f0c2a-0000-7000-8000-0000000000bb', moisture_pct: 41 }
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

/** A second client, the way a phone that closed the app comes back. */
function newClient(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryDefaults(STATUS_KEY, { meta: { persist: true } })
  return client
}

/** What actually landed on disk, or null when nothing did. */
async function persistedKeys(): Promise<unknown[]> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) return []
  const persisted = JSON.parse(row.value) as {
    clientState: { queries: { queryKey: unknown }[] }
  }
  return persisted.clientState.queries.map((query) => query.queryKey)
}

async function agedPersistedRow(ageMs: number): Promise<void> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) throw new Error('nothing was persisted')
  const persisted = JSON.parse(row.value) as Record<string, unknown>
  await db.queryCache.put({
    key: PERSIST_CACHE_KEY,
    value: JSON.stringify({ ...persisted, timestamp: Date.now() - ageMs }),
  })
}

/** Rewrites the envelope the persister owns, as an older or newer build would. */
async function rewritePersistedEnvelope(patch: { buster?: string }): Promise<void> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) throw new Error('nothing was persisted')
  const persisted = JSON.parse(row.value) as Record<string, unknown>
  await db.queryCache.put({
    key: PERSIST_CACHE_KEY,
    value: JSON.stringify({ ...persisted, ...patch }),
  })
}

function restoredClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

beforeEach(async () => {
  await resetLocalDb()
  queryClient.clear()
})

describe('persisted query cache (docs/07 §Flujo de datos y offline)', () => {
  it('restores a persisted query into a brand new client', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    before.setQueryData(ME_KEY, { id: 'user-1' })
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    const after = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: after })

    expect(after.getQueryData(STATUS_KEY)).toEqual(STATUS)
    // The query that never opted in is absent, not `undefined`-by-accident:
    // `['me', token]` was in the source client and still did not come back.
    expect(after.getQueryData(ME_KEY)).toBeUndefined()
  })

  it('writes only the queries that opted in with meta.persist', async () => {
    const client = newClient()
    client.setQueryData(STATUS_KEY, STATUS)
    client.setQueryData(ME_KEY, { id: 'user-1' })

    await persistQueryClientSave({ ...persistOptions, queryClient: client })

    expect(await persistedKeys()).toEqual([STATUS_KEY])
    // Auth data (the `me` seed) must never reach the disk, so its text is
    // absent from the serialized row, not just un-parsed.
    const row = await db.queryCache.get(PERSIST_CACHE_KEY)
    expect(row?.value).not.toContain('token-abc')
  })

  it('restores an entry inside the seven days and drops an older one', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    await agedPersistedRow(SEVEN_DAYS_MS - 60 * 1000)
    const fresh = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: fresh })
    expect(fresh.getQueryData(STATUS_KEY)).toEqual(STATUS)

    await agedPersistedRow(SEVEN_DAYS_MS + 60 * 1000)
    const stale = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: stale })
    expect(stale.getQueryData(STATUS_KEY)).toBeUndefined()
    // Expired means deleted, not merely ignored: the next app start pays no
    // storage for it.
    expect(await db.queryCache.get(PERSIST_CACHE_KEY)).toBeUndefined()
  })

  it('drops a cache a newer build no longer claims', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    await rewritePersistedEnvelope({ buster: 'the-build-that-wrote-this' })

    const after = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: after })

    expect(after.getQueryData(STATUS_KEY)).toBeUndefined()
    expect(await db.queryCache.get(PERSIST_CACHE_KEY)).toBeUndefined()
  })

  it('empties the persisted and the in-memory cache when the session is cleared', async () => {
    queryClient.setQueryDefaults(STATUS_KEY, { meta: { persist: true } })
    queryClient.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient })
    expect(await db.queryCache.count()).toBe(1)
    expect(queryClient.getQueryData(STATUS_KEY)).toEqual(STATUS)

    clearSession()

    await vi.waitFor(async () => {
      expect(await db.queryCache.count()).toBe(0)
    })
    // A shared phone: the next user must not read the previous one's plots,
    // on disk or in memory.
    expect(queryClient.getQueryData(STATUS_KEY)).toBeUndefined()
  })
})
