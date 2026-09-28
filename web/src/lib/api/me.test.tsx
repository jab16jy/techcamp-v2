import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from './session'
import { useActiveOrgRole } from './me'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useActiveOrgRole and session isolation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    clearSession()
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('after clearSession() useActiveOrgRole() is null and localStorage has no techcamp.me', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: 'u1',
        phone: '3001234567',
        email: null,
        full_name: 'Tech One',
        memberships: [{ org_id: 'org-1', role: 'technician' }],
      }),
    )

    setSession('token-1', 'org-1')

    const { result } = renderHook(() => useActiveOrgRole(), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(result.current).toBe('technician'))
    expect(localStorage.getItem('techcamp.me')).not.toBeNull()

    // Sign out / 401 path
    act(() => {
      clearSession()
    })

    // Negative assertion: role is null and localStorage has no techcamp.me
    expect(result.current).toBeNull()
    expect(localStorage.getItem('techcamp.me')).toBeNull()
  })

  it('a second user signing in never sees the first users role', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    // User 1 is a technician
    vi.mocked(fetch).mockImplementation(async (input) => {
      const authHeader =
        input instanceof Request
          ? input.headers.get('Authorization')
          : undefined
      if (authHeader === 'Bearer token-1') {
        return jsonResponse({
          id: 'u1',
          phone: '3001234567',
          email: null,
          full_name: 'Tech One',
          memberships: [{ org_id: 'org-1', role: 'technician' }],
        })
      }
      if (authHeader === 'Bearer token-2') {
        return jsonResponse({
          id: 'u2',
          phone: '3007654321',
          email: null,
          full_name: 'Producer Two',
          memberships: [{ org_id: 'org-1', role: 'producer' }],
        })
      }
      throw new Error(`unexpected request with auth: ${authHeader}`)
    })

    setSession('token-1', 'org-1')
    const { result, unmount } = renderHook(() => useActiveOrgRole(), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(result.current).toBe('technician'))

    // User 1 logs out
    act(() => {
      clearSession()
    })
    expect(result.current).toBeNull()
    unmount()

    // User 2 signs in on the same device
    setSession('token-2', 'org-1')
    const { result: user2Result } = renderHook(() => useActiveOrgRole(), {
      wrapper: wrapper(queryClient),
    })

    // User 2 must resolve to 'producer', never inheriting 'technician'
    await waitFor(() => expect(user2Result.current).toBe('producer'))
    expect(user2Result.current).not.toBe('technician')
  })

  it('maps unknown roles to null and types known roles', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: 'u3',
        phone: '3001112233',
        email: null,
        full_name: 'Auditor Three',
        memberships: [{ org_id: 'org-1', role: 'auditor' }],
      }),
    )

    setSession('token-3', 'org-1')

    const { result } = renderHook(() => useActiveOrgRole(), {
      wrapper: wrapper(queryClient),
    })

    // Wait for me to resolve; unknown role 'auditor' must map to null
    await waitFor(() => {
      expect(localStorage.getItem('techcamp.me')).not.toBeNull()
    })
    expect(result.current).toBeNull()
  })
})
