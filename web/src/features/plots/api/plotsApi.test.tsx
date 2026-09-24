import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GeoJsonPolygon } from '../polygon'
import { useCreateFarm, useCreatePlot, useFarms, usePlotsByFarm } from './plotsApi'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('plotsApi list refetch after create (#21 round 12)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("refetches the org's farms list after a successful farm create", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(
        jsonResponse({
          id: 'farm-1',
          org_id: 'org-1',
          name: 'Finca La Esperanza',
          municipality_code: '20001',
          location: { type: 'Point', coordinates: [-73.25, 10.46] },
          technician_id: null,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              id: 'farm-1',
              org_id: 'org-1',
              name: 'Finca La Esperanza',
              municipality_code: '20001',
              location: { type: 'Point', coordinates: [-73.25, 10.46] },
              technician_id: null,
            },
          ],
          next_cursor: null,
        }),
      )

    const { result } = renderHook(
      () => ({ farms: useFarms('org-1'), create: useCreateFarm('org-1') }),
      { wrapper: wrapper(queryClient) },
    )

    await waitFor(() => expect(result.current.farms.data?.farms).toHaveLength(0))

    await act(async () => {
      await result.current.create.mutateAsync({
        name: 'Finca La Esperanza',
        municipality_code: '20001',
        location: { type: 'Point', coordinates: [-73.25, 10.46] },
      })
    })

    await waitFor(() => expect(result.current.farms.data?.farms).toHaveLength(1))
    // 1 initial GET + 1 POST + 1 refetch GET: proves invalidation triggered a real refetch,
    // not just the mutation's own response being reused.
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3)
  })

  it("refetches a farm's plots list after a successful plot create", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const createdPlot = {
      id: 'plot-1',
      org_id: 'org-1',
      farm_id: 'farm-1',
      name: 'Lote Norte',
      boundary: {
        type: 'Polygon',
        coordinates: [
          [
            [-74, 10],
            [-73, 11],
            [-72, 12],
            [-74, 10],
          ],
        ],
      } satisfies GeoJsonPolygon,
      area_ha: 1,
      weather_cell_id: null,
      irrigation_system: 'none',
      irrigation_efficiency: null,
      system_flow_lph: null,
    }
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(createdPlot))
      .mockResolvedValueOnce(jsonResponse([createdPlot]))

    const { result } = renderHook(
      () => ({ plots: usePlotsByFarm(['farm-1']), create: useCreatePlot('farm-1') }),
      { wrapper: wrapper(queryClient) },
    )

    await waitFor(() => expect(result.current.plots[0]?.data).toHaveLength(0))

    await act(async () => {
      await result.current.create.mutateAsync({
        name: 'Lote Norte',
        boundary: createdPlot.boundary,
        irrigation_system: 'none',
        irrigation_efficiency: null,
        system_flow_lph: null,
      })
    })

    await waitFor(() => expect(result.current.plots[0]?.data).toHaveLength(1))
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3)
  })
})
