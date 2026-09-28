import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setSession } from '../../../lib/api/session'
import { registerPushSubscription } from './pushApi'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function requestBody(input: Request | string | URL): unknown {
  return input instanceof Request ? input.json() : Promise.resolve(null)
}

/** The session middleware re-wraps the call in a `Request`, so the verb and the
 * `Authorization` header live on that object rather than in a second argument. */
function requestOf(input: Request | string | URL): Request {
  if (!(input instanceof Request)) throw new Error('expected fetch to be called with a Request')
  return input
}

const SUBSCRIPTION = {
  endpoint: 'https://push.example.com/sub/abc',
  expirationTime: null,
  keys: { p256dh: 'p256dh-value', auth: 'auth-value' },
}

describe('registerPushSubscription', () => {
  beforeEach(() => {
    setSession('token-abc', 'org-1')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts exactly the documented body and returns the new id', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(jsonResponse({ id: 'sub-1' }, 201))

    const id = await registerPushSubscription({
      toJSON: () => SUBSCRIPTION,
    } as unknown as PushSubscription)

    expect(id).toBe('sub-1')
    const [input] = vi.mocked(globalThis.fetch).mock.calls[0]
    const request = requestOf(input)
    expect(requestUrl(input)).toContain('/api/v1/push-subscriptions')
    expect(request.method).toBe('POST')
    expect(await requestBody(input)).toEqual({
      endpoint: 'https://push.example.com/sub/abc',
      keys: { p256dh: 'p256dh-value', auth: 'auth-value' },
    })
    expect(request.headers.get('Authorization')).toBe('Bearer token-abc')
  })

  it('refuses a subscription the browser returned without keys, instead of posting a half record', async () => {
    await expect(
      registerPushSubscription({ toJSON: () => ({ endpoint: 'x', keys: null }) } as unknown as PushSubscription),
    ).rejects.toThrow(/keys/i)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
