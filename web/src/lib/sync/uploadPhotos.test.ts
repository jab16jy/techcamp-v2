import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from '../db/db'
import type { LogbookEntryRow, PhotoBytes, PhotoRow, SyncEntity } from '../db/db'
import { savePhoto } from '../db/photos'
import { resetLocalDb } from '../db/testDb'
import { setSession } from '../api/session'
import { syncOnce } from './synchronizer'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000dd'
const ENTRY_ID = '018f0c2a-0000-7000-8000-0000000000bb'
const UPLOAD_URL = 'http://minio.test/techcamp/0192f0c2a.webp'

interface PresignBody {
  logbook_entry_id?: string
  extension_visit_id?: string
  content_type: string
  bytes: number
}

interface PutCall {
  url: string
  method: string | undefined
  contentType: string | null
  hasContentLength: boolean
  body: unknown
}

/**
 * A fetch double for the whole run: `/push`, `/pull`, the presign call and the
 * PUT to object storage. Every photo call is recorded, so a test can prove what
 * was sent rather than only what came back.
 */
function stubApi(options: {
  presign?: (body: PresignBody, index: number) => Response
  put?: (url: string, index: number) => Response
}): { presignBodies: PresignBody[]; putCalls: PutCall[] } {
  const presignBodies: PresignBody[] = []
  const putCalls: PutCall[] = []
  let presignIndex = 0
  let putIndex = 0

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/attachments:presign')) {
        const raw = input instanceof Request ? await input.clone().text() : String(init?.body)
        const body = JSON.parse(raw) as PresignBody
        presignBodies.push(body)
        return (
          options.presign?.(body, presignIndex++) ??
          json({ upload_url: UPLOAD_URL, object_key: 'techcamp/0192f0c2a.webp' }, 201)
        )
      }
      if (init?.method === 'PUT') {
        const headers = new Headers(init.headers)
        putCalls.push({
          url,
          method: init.method,
          contentType: headers.get('Content-Type'),
          hasContentLength: headers.has('Content-Length'),
          body: init.body,
        })
        return options.put?.(url, putIndex++) ?? new Response('', { status: 200 })
      }
      if (url.endsWith('/push')) return json({ results: [] })
      return json({ changes: [], next_since: 0, has_more: false })
    }),
  )

  return { presignBodies, putCalls }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A problem+json body, the shape docs/04 uses for errors. */
function problem(status: number, title: string): Response {
  return new Response(JSON.stringify({ title, detail: title }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  })
}

/** An entry the server already has: a `server_version` and no queued change. */
async function syncedEntry(overrides: Partial<LogbookEntryRow> = {}): Promise<void> {
  await db.logbookEntries.put({
    id: ENTRY_ID,
    org_id: ORG_ID,
    plot_id: PLOT_ID,
    crop_cycle_id: null,
    kind: 'harvest',
    occurred_on: '2026-09-28',
    quantity: null,
    unit: null,
    cost_cop: null,
    yield_kg: 120,
    sold_kg: null,
    sale_price_cop_per_kg: null,
    labor_days: null,
    irrigation_mm: null,
    alert_id: null,
    notes: null,
    created_by: null,
    created_offline: false,
    client_updated_at: '2026-09-28T10:00:00.000Z',
    server_version: 7,
    deleted_at: null,
    syncState: 'synced',
    syncError: null,
    ...overrides,
  })
}

/** Bytes whose first eight are recognizable, so a swapped body is visible. */
function bytesOf(size: number): PhotoBytes {
  const bytes = new Uint8Array(new ArrayBuffer(size))
  for (let index = 0; index < Math.min(size, 8); index += 1) bytes[index] = index + 1
  return bytes
}

async function pendingPhoto(
  id: string,
  size: number,
  entity: SyncEntity = 'logbook_entry',
): Promise<PhotoRow> {
  return savePhoto({
    id,
    entity,
    parent_id: ENTRY_ID,
    data: bytesOf(size),
    content_type: 'image/webp',
    bytes: size,
  })
}

async function photoRow(id: string): Promise<PhotoRow> {
  const row = await db.photos.get(id)
  if (row === undefined) throw new Error(`photo ${id} is gone`)
  return row
}

/** Where `expireSession` sent the user (D11); jsdom cannot navigate. */
const realLocation = Object.getOwnPropertyDescriptor(window, 'location')
let navigations: string[] = []

beforeEach(async () => {
  await resetLocalDb()
  setSession('test-token', null)
  navigations = []
  Object.defineProperty(window, 'location', {
    value: {
      assign: (url: string | URL) => {
        navigations.push(String(url))
      },
    },
    writable: true,
    configurable: true,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  Object.defineProperty(window, 'location', realLocation as PropertyDescriptor)
})

describe('uploading a photo of a synced entry', () => {
  it('presigns the photo\'s own bytes and PUTs exactly those bytes to that URL', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 150 * 1024)
    const before = await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')
    const { presignBodies, putCalls } = stubApi({})

    await syncOnce()

    expect(presignBodies).toEqual([
      { logbook_entry_id: ENTRY_ID, content_type: 'image/webp', bytes: 150 * 1024 },
    ])
    // The declared length is the stored length, never a number typed twice.
    expect(presignBodies[0].bytes).toBe(before.data?.byteLength)
    expect(putCalls).toHaveLength(1)
    const put = putCalls[0]
    expect(put.url).toBe(UPLOAD_URL)
    expect(put.method).toBe('PUT')
    expect(put.contentType).toBe('image/webp')
    // The presigned URL signs Content-Length, and Content-Length is a forbidden
    // header: the browser derives it from the body, so the body must be the
    // very bytes that were presigned — not only of the same length.
    expect(put.hasContentLength).toBe(false)
    expect(put.body).toBeInstanceOf(Blob)
    expect((put.body as Blob).size).toBe(presignBodies[0].bytes)
    const sent = new Uint8Array(await (put.body as Blob).arrayBuffer())
    expect([...sent.slice(0, 8)]).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
    const stored = await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')
    expect(stored.status).toBe('uploaded')
    // The object is in storage, so the phone stops carrying the bytes.
    expect(stored.data).toBeNull()
  })
})

describe('a photo whose parent is not synced yet', () => {
  it('is left alone: no presign, no PUT, still pending', async () => {
    await syncedEntry({ server_version: null, syncState: 'pending' })
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { presignBodies, putCalls } = stubApi({})

    await syncOnce()

    expect(presignBodies).toEqual([])
    expect(putCalls).toEqual([])
    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')).status).toBe('pending')
  })
})

describe('a photo whose parent has a change still queued', () => {
  it('is not presigned, because the server may not have this version yet', async () => {
    await syncedEntry({ syncState: 'pending' })
    await db.outbox.add({
      id: ENTRY_ID,
      entity: 'logbook_entry',
      op: 'upsert',
      data: {} as never,
      client_updated_at: '2026-09-28T12:00:00.000Z',
      status: 'pending',
      error: null,
    })
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { presignBodies } = stubApi({})

    await syncOnce()

    expect(presignBodies).toEqual([])
    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')).status).toBe('pending')
  })
})

describe('presign answers the server can give', () => {
  it('404 keeps the photo pending, because the parent is not visible yet', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { putCalls } = stubApi({ presign: () => problem(404, 'Logbook entry not found') })

    await syncOnce()

    expect(putCalls).toEqual([])
    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')).status).toBe('pending')
  })

  it('422 marks the photo failed with the reason the user is shown', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { putCalls } = stubApi({ presign: () => problem(422, 'bytes must be <= 204800') })

    await syncOnce()

    expect(putCalls).toEqual([])
    const stored = await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')
    expect(stored.status).toBe('failed')
    expect(stored.error).toBe('bytes must be <= 204800')
  })
})

describe('a PUT that does not land', () => {
  it('keeps the photo pending for the next run instead of losing it', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    stubApi({ put: () => new Response('', { status: 500 }) })

    await syncOnce()

    const stored = await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')
    expect(stored.status).toBe('pending')
    expect(stored.error).toBeNull()
    expect(stored.data?.byteLength).toBe(1024)
  })
})

describe('an expired session during the photo queue (D11)', () => {
  it('stops the run with the photo still pending and the session expired', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { putCalls } = stubApi({ presign: () => problem(401, 'Not authenticated') })

    const outcome = await syncOnce()

    expect(outcome).toEqual({ status: 'stopped', reason: 'unauthorized' })
    expect(navigations).toEqual(['/ingreso'])
    expect(putCalls).toEqual([])
    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')).status).toBe('pending')
  })
})

describe('one photo that cannot be uploaded', () => {
  it('does not stop the next one, nor the run', async () => {
    await syncedEntry()
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a2', 2048)
    stubApi({
      presign: (_body, index) =>
        index === 0 ? problem(422, 'bytes must be <= 204800') : json({ upload_url: UPLOAD_URL, object_key: 'k' }, 201),
    })

    const outcome = await syncOnce()

    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a1')).status).toBe('failed')
    expect((await photoRow('0192f0c2a-0000-7000-8000-0000000000a2')).status).toBe('uploaded')
    expect(outcome.status).toBe('synced')
  })
})

describe('a photo of a parent this device deleted (D13)', () => {
  it('is dropped locally, and never presigned', async () => {
    await syncedEntry({ deleted_at: '2026-09-28T12:00:00.000Z' })
    await pendingPhoto('0192f0c2a-0000-7000-8000-0000000000a1', 1024)
    const { presignBodies } = stubApi({})

    await syncOnce()

    expect(presignBodies).toEqual([])
    expect(await db.photos.count()).toBe(0)
  })
})
