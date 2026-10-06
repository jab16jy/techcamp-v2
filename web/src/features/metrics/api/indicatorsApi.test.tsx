/**
 * The two persisted reads behind "Indicadores de tecnificación"
 * (docs/04-api.md:233-237). Both keys start with the organization — the rule
 * `queryClient.ts` documents — both opt into persistence explicitly, and both
 * ask for one calendar month in `YYYY-MM`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import {
  isMonthWithoutReport,
  orgIndicatorsKey,
  plotIndicatorsKey,
  previousMonthInBogota,
  useOrgIndicators,
  usePlotIndicators,
} from './indicatorsApi'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const PLOT_INDICATORS = {
  plot_id: 'plot-1',
  month: '2026-09-01',
  monitoring: 0.9,
  record_keeping: 0.5,
  decision: null,
  risk_management: null,
  digital_adoption_index: 70,
  computed_at: '2026-10-01T07:00:00Z',
}

const ORG_INDICATORS = {
  org_id: 'org-1',
  month: '2026-09-01',
  mean_digital_adoption_index: 70,
  plots_with_index: 3,
  monitored_plots_ratio: 0.5,
  harvested_cycles_ratio: null,
  median_hours_to_first_reading: null,
}

function renderPlotIndicators(orgId: string | null, plotId: string | null, month: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, ...renderHook(() => usePlotIndicators(orgId, plotId, month), { wrapper }) }
}

function renderOrgIndicators(orgId: string | null, month: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, ...renderHook(() => useOrgIndicators(orgId, month), { wrapper }) }
}

describe('previousMonthInBogota', () => {
  it('asks for the previous calendar month in America/Bogota, not in UTC', () => {
    // 07:00 in Bogotá on 2026-10-05.
    expect(previousMonthInBogota(new Date('2026-10-05T12:00:00Z'))).toBe('2026-09')
  })

  it('rolls back across the year boundary', () => {
    // 07:00 in Bogotá on 2027-01-01 → December 2026, not 2027-01.
    expect(previousMonthInBogota(new Date('2027-01-01T12:00:00Z'))).toBe('2026-12')
  })

  it('reads the Bogotá month, not the UTC one, where the two disagree', () => {
    // 21:00 in Bogotá on 2026-09-30 while UTC has already entered October: the
    // asked month is August. Reading `getMonth()` here would answer September.
    expect(previousMonthInBogota(new Date('2026-10-01T02:00:00Z'))).toBe('2026-08')
    // 01:30 in Bogotá on 2026-10-01 — the job's own hour, five hours into the
    // day while UTC is still on the 30th.
    expect(previousMonthInBogota(new Date('2026-10-01T06:30:00Z'))).toBe('2026-09')
  })
})

describe('usePlotIndicators', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('reads GET /plots/{plot_id}/metrics for the asked month', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PLOT_INDICATORS))

    const { result } = renderPlotIndicators('org-1', 'plot-1', '2026-09')

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const url = requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)
    expect(url).toContain('/api/v1/plots/plot-1/metrics')
    expect(url).toContain('month=2026-09')
    expect(result.current.data?.digital_adoption_index).toBe(70)
    // A component without evidence stays null on the wire (D-T0.3).
    expect(result.current.data?.decision).toBeNull()
  })

  it('keys with the organization first and opts into persistence, as the cache requires', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PLOT_INDICATORS))

    const { queryClient } = renderPlotIndicators('org-1', 'plot-1', '2026-09')

    await waitFor(() =>
      expect(queryClient.getQueryData(['org-1', 'plot-indicators', 'plot-1', '2026-09'])).toBeDefined(),
    )
    expect(queryClient.getQueryData(['plot-indicators', 'plot-1', '2026-09'])).toBeUndefined()
    expect(plotIndicatorsKey('org-1', 'plot-1', '2026-09')).toEqual([
      'org-1',
      'plot-indicators',
      'plot-1',
      '2026-09',
    ])
    const query = queryClient
      .getQueryCache()
      .find({ queryKey: ['org-1', 'plot-indicators', 'plot-1', '2026-09'] })
    expect(query?.meta?.persist).toBe(true)
  })

  it('fetches nothing before an organization or a plot is known', async () => {
    const { result } = renderPlotIndicators(null, null, '2026-09')

    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('surfaces the server error instead of an empty month', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Plot has no metrics for that month', status: 404 }, 404),
    )

    const { result } = renderPlotIndicators('org-1', 'plot-1', '2026-09')

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(isMonthWithoutReport(result.current.error)).toBe(true)
  })
})

describe('useOrgIndicators', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('reads GET /organizations/{org_id}/metrics for the asked month', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(ORG_INDICATORS))

    const { result } = renderOrgIndicators('org-1', '2026-09')

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const url = requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)
    expect(url).toContain('/api/v1/organizations/org-1/metrics')
    expect(url).toContain('month=2026-09')
    expect(result.current.data?.mean_digital_adoption_index).toBe(70)
    // D-T7.1: two figures stay null until their views land — never 0.
    expect(result.current.data?.harvested_cycles_ratio).toBeNull()
    expect(result.current.data?.median_hours_to_first_reading).toBeNull()
  })

  it('keys with the organization first and opts into persistence, as the cache requires', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(ORG_INDICATORS))

    const { queryClient } = renderOrgIndicators('org-1', '2026-09')

    await waitFor(() =>
      expect(queryClient.getQueryData(['org-1', 'org-indicators', '2026-09'])).toBeDefined(),
    )
    expect(queryClient.getQueryData(['org-indicators', '2026-09'])).toBeUndefined()
    expect(orgIndicatorsKey('org-1', '2026-09')).toEqual(['org-1', 'org-indicators', '2026-09'])
    const query = queryClient.getQueryCache().find({ queryKey: ['org-1', 'org-indicators', '2026-09'] })
    expect(query?.meta?.persist).toBe(true)
  })

  it('fetches nothing before an organization is known', async () => {
    const { result } = renderOrgIndicators(null, '2026-09')

    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(fetch).not.toHaveBeenCalled()
  })
})

describe('isMonthWithoutReport', () => {
  it('reads the month the job never stored as absent, not as a failure', () => {
    expect(
      isMonthWithoutReport({ status: 404, title: 'Plot has no metrics for that month' }),
    ).toBe(true)
  })

  it('does not swallow a plot the caller may not see', () => {
    expect(isMonthWithoutReport({ status: 404, title: 'Plot not found' })).toBe(false)
    expect(isMonthWithoutReport({ status: 403, title: 'Plot has no metrics for that month' })).toBe(
      false,
    )
    expect(isMonthWithoutReport(new Error('offline'))).toBe(false)
    expect(isMonthWithoutReport(null)).toBe(false)
  })
})
