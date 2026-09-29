import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../api/client'
import { clearSession, setSession } from '../api/session'
import { pullChanges, pushChanges, SyncStoppedError } from './transport'

describe('transport through apiClient', () => {
  beforeEach(() => {
    setSession('test-token', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('calls apiClient.POST for pushChanges, returning data on success and throwing SyncStoppedError on 401', async () => {
    const postSpy = vi.spyOn(apiClient, 'POST')

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input)
        if (url.includes('/api/v1/sync/push')) {
          return new Response(JSON.stringify({ results: [{ id: '1', status: 'applied', server_version: 1 }] }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(null, { status: 404 })
      }),
    )

    const pushReq = { device_id: 'dev-1', changes: [] }
    const result = await pushChanges(pushReq)

    // Positive: apiClient.POST was called and returns data
    expect(postSpy).toHaveBeenCalledWith('/api/v1/sync/push', expect.objectContaining({ body: pushReq }))
    expect(result).toEqual({ results: [{ id: '1', status: 'applied', server_version: 1 }] })

    // Negative: 401 stops with SyncStoppedError('unauthorized', 401)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    await expect(pushChanges(pushReq)).rejects.toThrow(SyncStoppedError)
    await expect(pushChanges(pushReq)).rejects.toMatchObject({ reason: 'unauthorized', status: 401 })
  })

  it('calls apiClient.GET for pullChanges and handles non-JSON 2xx as unavailable', async () => {
    const getSpy = vi.spyOn(apiClient, 'GET')

    // Non-JSON 2xx (e.g. captive portal returning HTML)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<!doctype html><title>Portal</title>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      })),
    )

    await expect(pullChanges(0, 10)).rejects.toThrow(SyncStoppedError)
    await expect(pullChanges(0, 10)).rejects.toMatchObject({ reason: 'unavailable' })
    expect(getSpy).toHaveBeenCalledWith(
      '/api/v1/sync/pull',
      expect.objectContaining({ params: { query: { since: 0, limit: 10 } } }),
    )

    // Positive: valid JSON 2xx succeeds
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ changes: [], next_since: 0, has_more: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })),
    )
    const valid = await pullChanges(0, 10)
    expect(valid).toEqual({ changes: [], next_since: 0, has_more: false })
  })
})
