import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, ApiError } from './client'
import { clearSession, setSession } from './session'

describe('apiFetch', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('sends the bearer token when a session exists', async () => {
    setSession('token-123', 'org-1')
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await apiFetch('/me')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer token-123')
  })

  it('omits the Authorization header when there is no session', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await apiFetch('/me')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.has('Authorization')).toBe(false)
  })

  it('sends a JSON body with Content-Type when given one', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    await apiFetch('/dev/auth/otp', { method: 'POST', body: { phone: '3001234567' } })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init?.body).toBe(JSON.stringify({ phone: '3001234567' }))
  })

  it('returns undefined for a 204 response', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    await expect(apiFetch('/dev/auth/otp', { method: 'POST', body: {} })).resolves.toBeUndefined()
  })

  it('throws an ApiError with the problem+json title and detail', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Invalid or expired code',
          status: 401,
          detail: 'The OTP code is invalid or has expired.',
        }),
        { status: 401, headers: { 'content-type': 'application/problem+json' } },
      ),
    )

    await expect(apiFetch('/dev/auth/otp/verify', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 401,
      title: 'Invalid or expired code',
      detail: 'The OTP code is invalid or has expired.',
    })
  })

  it('throws an ApiError from FastAPI default validation error shape', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({ detail: [{ loc: ['query', 'org_id'], msg: 'Field required', type: 'missing' }] }),
        { status: 422 },
      ),
    )

    const error = await apiFetch('/farms').catch((err: unknown) => err)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(422)
    expect((error as ApiError).title).toBe('Field required')
  })
})
