import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePlotBaseline, usePutBaseline } from './baselineApi'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function problemResponse(status: number, title: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title, status }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

const SURVEY = {
  plot_id: 'plot-1',
  org_id: 'org-1',
  enrolled_on: '2026-01-15',
  crop_id: 2,
  last_yield_kg_ha: 2400,
  last_cost_cop_ha: null,
  irrigation_practice: 'drip',
  recorded_by: 'user-1',
}

function newQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

describe('usePlotBaseline', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reads the survey of a plot and keeps it in the persisted cache', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(SURVEY))
    const queryClient = newQueryClient()

    const { result } = renderHook(() => usePlotBaseline('org-1', 'plot-1'), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(result.current.data).toEqual(SURVEY))
    // docs/07 §Flujo de datos y offline: the phone keeps the last state, so the
    // survey survives a reload without a connection.
    expect(queryClient.getQueryCache().find({ queryKey: ['org-1', 'baseline', 'plot-1'] })?.meta).toEqual({
      persist: true,
    })
    expect(requestOf(vi.mocked(fetch).mock.calls[0][0] as Request).url).toContain(
      '/api/v1/plots/plot-1/baseline',
    )
  })

  it('surfaces the 404 of a plot without a survey, so the caller can invite to register', async () => {
    vi.mocked(fetch).mockResolvedValue(problemResponse(404, 'Plot has no enrollment survey'))
    const queryClient = newQueryClient()

    const { result } = renderHook(() => usePlotBaseline('org-1', 'plot-1'), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toMatchObject({ status: 404 })
  })

  it('never fetches without an org, the same gate the persisted plot status uses', () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(SURVEY))
    const queryClient = newQueryClient()

    renderHook(() => usePlotBaseline(null, 'plot-1'), { wrapper: wrapper(queryClient) })

    expect(fetch).not.toHaveBeenCalled()
  })
})

describe('usePutBaseline', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the survey body and caches the saved row under the read key', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(SURVEY))
    const queryClient = newQueryClient()

    const { result } = renderHook(() => usePutBaseline('org-1', 'plot-1'), {
      wrapper: wrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        enrolled_on: '2026-01-15',
        crop_id: 2,
        last_yield_kg_ha: 2400,
        last_cost_cop_ha: null,
        irrigation_practice: 'drip',
      })
    })

    const request = requestOf(vi.mocked(fetch).mock.calls[0][0] as Request)
    expect(request.method).toBe('PUT')
    expect(request.url).toContain('/api/v1/plots/plot-1/baseline')
    // An unknown cost is missing evidence, not a free plot (docs/03:426-430).
    expect(await request.json()).toEqual({
      enrolled_on: '2026-01-15',
      crop_id: 2,
      last_yield_kg_ha: 2400,
      last_cost_cop_ha: null,
      irrigation_practice: 'drip',
    })
    // The write answers with the stored row, so the read shows it without a refetch.
    expect(queryClient.getQueryData(['org-1', 'baseline', 'plot-1'])).toEqual(SURVEY)
  })

  it('propagates the 403 of a read-only role for the caller to explain', async () => {
    vi.mocked(fetch).mockResolvedValue(
      problemResponse(403, 'Role cannot save this enrollment survey'),
    )
    const queryClient = newQueryClient()

    const { result } = renderHook(() => usePutBaseline('org-1', 'plot-1'), {
      wrapper: wrapper(queryClient),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({
          enrolled_on: '2026-01-15',
          crop_id: 2,
          last_yield_kg_ha: 2400,
          irrigation_practice: 'none',
        }),
      ).rejects.toMatchObject({ status: 403 })
    })
  })
})