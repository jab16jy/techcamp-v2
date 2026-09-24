import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient, ApiError, REQUEST_TIMEOUT_MS } from './client'
import { clearSession, getToken, setSession } from './session'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('apiClient', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('sends the bearer token when a session exists', async () => {
    setSession('token-123', 'org-1')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 'u1', memberships: [] }))

    await apiClient.GET('/api/v1/me', {})

    const [request] = vi.mocked(fetch).mock.calls[0]
    expect((request as Request).headers.get('Authorization')).toBe('Bearer token-123')
  })

  it('omits the Authorization header when there is no session', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 'u1', memberships: [] }))

    await apiClient.GET('/api/v1/me', {})

    const [request] = vi.mocked(fetch).mock.calls[0]
    expect((request as Request).headers.has('Authorization')).toBe(false)
  })

  it('returns undefined data for a 204 response', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    const { data, error } = await apiClient.POST('/api/v1/dev/auth/otp', { body: { phone: '3001234567' } })

    expect(data).toBeUndefined()
    expect(error).toBeUndefined()
  })

  it('throws an ApiError with the problem+json title and detail', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          type: 'about:blank',
          title: 'Invalid or expired code',
          status: 401,
          detail: 'The OTP code is invalid or has expired.',
        },
        401,
      ),
    )

    await expect(
      apiClient.POST('/api/v1/dev/auth/otp/verify', { body: { phone: '3001234567', code: '000000' } }),
    ).rejects.toMatchObject({
      status: 401,
      title: 'Invalid or expired code',
      detail: 'The OTP code is invalid or has expired.',
    })
  })

  it('throws an ApiError from the FastAPI default validation error shape', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        { detail: [{ loc: ['query', 'org_id'], msg: 'Field required', type: 'missing' }] },
        422,
      ),
    )

    const error = await apiClient.GET('/api/v1/farms', { params: { query: { org_id: 'org-1' } } }).catch(
      (err: unknown) => err,
    )

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(422)
    expect((error as ApiError).title).toBe('Field required')
  })

  it('signs out and redirects to sign-in on a 401 for an authenticated request', async () => {
    setSession('token-123', 'org-1')
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Invalid token', status: 401 }, 401),
    )

    await expect(apiClient.GET('/api/v1/me', {})).rejects.toThrow()

    expect(getToken()).toBeNull()
    expect(assign).toHaveBeenCalledWith('/ingreso')
  })

  it('does not sign out on a 401 for an unauthenticated request', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Invalid or expired code', status: 401 }, 401),
    )

    await expect(
      apiClient.POST('/api/v1/dev/auth/otp/verify', { body: { phone: '3001234567', code: '000000' } }),
    ).rejects.toThrow()

    expect(assign).not.toHaveBeenCalled()
  })

  it('does not attach a stale stored token to an anonymous OTP verify call', async () => {
    setSession('stale-token', 'org-1')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 'u1', memberships: [] }))

    await apiClient.POST('/api/v1/dev/auth/otp/verify', { body: { phone: '3001234567', code: '000000' } })

    const [request] = vi.mocked(fetch).mock.calls[0]
    expect((request as Request).headers.has('Authorization')).toBe(false)
  })

  it('does not attach a stale stored token to an anonymous OTP request call', async () => {
    setSession('stale-token', 'org-1')
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    await apiClient.POST('/api/v1/dev/auth/otp', { body: { phone: '3001234567' } })

    const [request] = vi.mocked(fetch).mock.calls[0]
    expect((request as Request).headers.has('Authorization')).toBe(false)
  })

  it('does not sign out or clear a stale session when a wrong code 401s', async () => {
    setSession('stale-token', 'org-1')
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          type: 'about:blank',
          title: 'Invalid or expired code',
          status: 401,
          detail: 'El código es incorrecto o venció.',
        },
        401,
      ),
    )

    await expect(
      apiClient.POST('/api/v1/dev/auth/otp/verify', { body: { phone: '3001234567', code: '000000' } }),
    ).rejects.toMatchObject({ status: 401, detail: 'El código es incorrecto o venció.' })

    expect(getToken()).toBe('stale-token')
    expect(assign).not.toHaveBeenCalled()
  })

  it('rejects with a TimeoutError once a stalled request passes REQUEST_TIMEOUT_MS', async () => {
    vi.useFakeTimers()
    // `AbortSignal.timeout`'s own internal scheduling isn't guaranteed to be
    // driven by the timers vitest fakes, so it's stubbed with an equivalent
    // built from `setTimeout`, which fake timers do control (find-docs/ctx7:
    // vitest.dev/guide/mocking/timers).
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms: number) => {
      const controller = new AbortController()
      setTimeout(
        () => controller.abort(new DOMException('The operation timed out.', 'TimeoutError')),
        ms,
      )
      return controller.signal
    })
    // Cleanup lives in `finally`: a failed assertion above must not leak
    // fake timers or the spy into later tests (#21 round 13 WARNING).
    try {
      // A stalled fetch: it only ever settles if its request signal aborts.
      vi.mocked(fetch).mockImplementation(
        (input: RequestInfo | URL) =>
          new Promise((_resolve, reject) => {
            const request = input as Request
            request.signal.addEventListener('abort', () => reject(request.signal.reason as Error))
          }),
      )

      const pending = apiClient.GET('/api/v1/me', {})
      const assertion = expect(pending).rejects.toMatchObject({ name: 'TimeoutError' })
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS)
      await assertion
    } finally {
      timeoutSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('falls back to a timeout-only signal when AbortSignal.any is unavailable (older WebViews)', async () => {
    const originalAny = AbortSignal.any
    // @ts-expect-error simulating a WebView without AbortSignal.any (#21 round 12)
    delete AbortSignal.any
    // Proves the timeout-only path itself, not just "the request didn't throw"
    // (#21 round 13 SUGGESTION): the fallback must still build its signal from
    // `AbortSignal.timeout(REQUEST_TIMEOUT_MS)`, the same call the combined
    // (`AbortSignal.any`) path makes.
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 'u1', memberships: [] }))

    try {
      await apiClient.GET('/api/v1/me', {})
      expect(timeoutSpy).toHaveBeenCalledWith(REQUEST_TIMEOUT_MS)
      const [request] = vi.mocked(fetch).mock.calls[0]
      expect((request as Request).signal).toBeInstanceOf(AbortSignal)
      expect((request as Request).signal.aborted).toBe(false)
    } finally {
      timeoutSpy.mockRestore()
      AbortSignal.any = originalAny
    }
  })

  it('preserves a caller-supplied abort signal alongside the timeout', async () => {
    const controller = new AbortController()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const request = input as Request
      if (request.signal.aborted) throw new DOMException('Aborted', 'AbortError')
      return jsonResponse({ id: 'u1', memberships: [] })
    })

    controller.abort()
    await expect(apiClient.GET('/api/v1/me', { signal: controller.signal })).rejects.toThrow()
  })
})
