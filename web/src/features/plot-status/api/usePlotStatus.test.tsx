/**
 * docs/04 §Estado de la parcela + docs/07 §Flujo de datos y offline: the one
 * persisted query of the home screen. Its key starts with the organization
 * (the rule `queryClient.ts` documents) and it opts in explicitly.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { plotStatusKey, usePlotStatus } from './usePlotStatus'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const PLOT_STATUS = {
  plot: {
    id: 'plot-1',
    org_id: 'org-1',
    farm_id: 'farm-1',
    name: 'Lote Norte',
    area_ha: 1.5,
    irrigation_system: 'drip',
  },
  active_cycle: null,
  latest: { soil_moisture_pct: null, air_temp_c: null, air_rh_pct: null, at: null },
  water_balance: null,
  recommendation: null,
  open_alerts: [],
  weather_next_3d: [],
  nodes: [],
  digital_adoption_index: null,
}

function renderUsePlotStatus(orgId: string | null, plotId: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, ...renderHook(() => usePlotStatus(orgId, plotId), { wrapper }) }
}

describe('usePlotStatus', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('reads GET /plots/{plot_id}/status for the active plot', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PLOT_STATUS))

    const { result } = renderUsePlotStatus('org-1', 'plot-1')

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)).toContain(
      '/api/v1/plots/plot-1/status',
    )
    expect(result.current.data?.plot.name).toBe('Lote Norte')
  })

  it('keys the query with the organization first, as the persisted cache requires', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PLOT_STATUS))

    const { queryClient } = renderUsePlotStatus('org-1', 'plot-1')

    await waitFor(() => expect(queryClient.getQueryData(['org-1', 'status', 'plot-1'])).toBeDefined())
    // Negative: the plot id alone is not a key, so one org's plot can never be
    // served from another org's cache entry.
    expect(queryClient.getQueryData(['status', 'plot-1'])).toBeUndefined()
    expect(plotStatusKey('org-1', 'plot-1')).toEqual(['org-1', 'status', 'plot-1'])
  })

  it('opts the query into persistence, so the home screen opens offline', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PLOT_STATUS))

    const { queryClient } = renderUsePlotStatus('org-1', 'plot-1')

    await waitFor(() => expect(queryClient.getQueryData(['org-1', 'status', 'plot-1'])).toBeDefined())
    const query = queryClient.getQueryCache().find({ queryKey: ['org-1', 'status', 'plot-1'] })
    expect(query?.meta?.persist).toBe(true)
  })

  it('fetches nothing before an organization or a plot is known', async () => {
    const { result } = renderUsePlotStatus(null, null)

    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('surfaces the server error instead of an empty status', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Plot not found', status: 404 }, 404),
    )

    const { result } = renderUsePlotStatus('org-1', 'plot-1')

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })
})
