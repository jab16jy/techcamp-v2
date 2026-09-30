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
import { clearSession, setSession } from './session'

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
const TOKEN = 'token-abc'

/** `session.ts`'s own `TOKEN_KEY`, which it keeps private; a test has to write it
 * directly to reproduce the window it cannot offer an API for. */
const TOKEN_KEY = 'techcamp.token'

/** The row wraps the persister's JSON in the session that wrote it, so a test
 * that reads or rewrites the envelope has to unwrap it the way `getItem` does. */
function unwrap(value: string): { session: string; cache: string } {
  return JSON.parse(value) as { session: string; cache: string }
}

function envelopeOf(value: string): Record<string, unknown> {
  return JSON.parse(unwrap(value).cache) as Record<string, unknown>
}

function rewrap(value: string, cache: Record<string, unknown>): string {
  return JSON.stringify({ session: unwrap(value).session, cache: JSON.stringify(cache) })
}

/** A second client, the way a phone that closed the app comes back. */
function newClient(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryDefaults(STATUS_KEY, { meta: { persist: true } })
  return client
}

/** What actually landed on disk, or nothing when nothing did. */
async function persistedKeys(): Promise<unknown[]> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) return []
  const { clientState } = envelopeOf(row.value) as {
    clientState: { queries: { queryKey: unknown }[] }
  }
  return clientState.queries.map((query) => query.queryKey)
}

async function agedPersistedRow(ageMs: number): Promise<void> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) throw new Error('nothing was persisted')
  const envelope = envelopeOf(row.value)
  await db.queryCache.put({
    key: PERSIST_CACHE_KEY,
    value: rewrap(row.value, { ...envelope, timestamp: Date.now() - ageMs }),
  })
}

/** Rewrites the envelope the persister owns, as an older or newer build would. */
async function rewritePersistedEnvelope(patch: { buster?: string }): Promise<void> {
  const row = await db.queryCache.get(PERSIST_CACHE_KEY)
  if (row === undefined) throw new Error('nothing was persisted')
  await db.queryCache.put({
    key: PERSIST_CACHE_KEY,
    value: rewrap(row.value, { ...envelopeOf(row.value), ...patch }),
  })
}

function restoredClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

beforeEach(async () => {
  await resetLocalDb()
  queryClient.clear()
  // A signed-in phone is the state every restore below starts from: the cache
  // is restored for whoever holds a session, so no test may rely on there
  // being none.
  localStorage.removeItem(TOKEN_KEY)
  setSession(TOKEN, ORG_ID)
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
    // absent from the persisted cache, not just un-parsed. Read through the
    // wrapper: `row.value` does carry the token, because binding the row to
    // the session that wrote it is exactly what the wrapper is for.
    const row = await db.queryCache.get(PERSIST_CACHE_KEY)
    const cache = row === undefined ? '' : unwrap(row.value).cache
    expect(cache).not.toContain('token-abc')
    expect(cache).not.toContain('user-1')
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

  it('neither restores nor keeps a persisted cache when no session token exists', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    // The control, same setup: with the session's own token present, the cache
    // restores. Only the token differs between this and the restore below.
    const signedIn = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: signedIn })
    expect(signedIn.getQueryData(STATUS_KEY)).toEqual(STATUS)

    // The window `clearSession` opens (#204): the token is gone from storage
    // but the process died before its fire-and-forget delete landed. Removing
    // the key alone reproduces it exactly — calling `clearSession()` here would
    // race its own wipe and could pass without the fix.
    localStorage.removeItem(TOKEN_KEY)

    const signedOut = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: signedOut })

    expect(signedOut.getQueryData(STATUS_KEY)).toBeUndefined()
    expect(await db.queryCache.get(PERSIST_CACHE_KEY)).toBeUndefined()
  })

  it('never stores the raw session token beside the cache', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    const row = await db.queryCache.get(PERSIST_CACHE_KEY)
    if (row === undefined) throw new Error('nothing was persisted')

    // The WHOLE stored value, not just the cache text: the row is the one place
    // a second long-lived copy of a bearer credential could hide, and sign-out
    // wipes this cache so the phone stops holding the previous user's secrets.
    expect(row.value).not.toContain(TOKEN)
    // Bound to that session still, by a digest rather than by the secret.
    const stored = unwrap(row.value)
    expect(stored.session).not.toBe(TOKEN)
    expect(stored.session).toMatch(/^[0-9a-f]{64}$/)

    // The control: a digest still restores its own session.
    const same = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: same })
    expect(same.getQueryData(STATUS_KEY)).toEqual(STATUS)

    // And the negative: a different user still gets nothing.
    setSession('token-of-another-user', ORG_ID)
    const other = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: other })
    expect(other.getQueryData(STATUS_KEY)).toBeUndefined()
    expect(await db.queryCache.get(PERSIST_CACHE_KEY)).toBeUndefined()
  })

  it('neither restores nor keeps a cache another session wrote', async () => {
    const before = newClient()
    before.setQueryData(STATUS_KEY, STATUS)
    await persistQueryClientSave({ ...persistOptions, queryClient: before })

    // The control, same setup and the same session: the cache restores.
    const owner = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: owner })
    expect(owner.getQueryData(STATUS_KEY)).toEqual(STATUS)

    // A shared phone: the OS killed the wipe, and a DIFFERENT user signed in.
    // Their token is a valid token, so only the session the row was written
    // under can say no. Org-scoped keys do not help here — a technician and a
    // producer sharing one device are in the same org, so the keys match.
    setSession('token-of-another-user', ORG_ID)

    const other = restoredClient()
    await persistQueryClientRestore({ ...persistOptions, queryClient: other })

    expect(other.getQueryData(STATUS_KEY)).toBeUndefined()
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
