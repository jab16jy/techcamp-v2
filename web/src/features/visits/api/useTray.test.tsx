/**
 * docs/04 §Visitas de extensión y bandeja del técnico + docs/07 §Flujo de datos y offline:
 * the persisted tray query. Its key starts with the organization (the rule queryClient.ts
 * documents) and it opts in with meta: { persist: true }.
 */
import { QueryClient, QueryClientProvider, dehydrate } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { persistOptions } from '../../../lib/api/queryClient'
import { clearSession, setSession } from '../../../lib/api/session'
import { seedTrayForOrg, trayKey, useTray, type TrayItem } from './useTray'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const TRAY_DATA: TrayItem[] = [
  {
    farm: {
      id: 'farm-1',
      org_id: 'org-1',
      name: 'Finca La Esperanza',
      municipality_code: '08001',
    },
    open_alerts: [
      {
        id: 'alert-1',
        org_id: 'org-1',
        rule_id: 'rule-1',
        rule_code: 'water_stress',
        plot_id: 'plot-1',
        node_id: null,
        state: 'open',
        severity: 'critical',
        evidence: {},
        opened_at: '2026-09-30T10:00:00Z',
        acknowledged_at: null,
        resolved_at: null,
        escalated_at: null,
        resolution_note: null,
      },
    ],
    last_visit_on: '2026-09-15',
  },
]

function renderUseTray(orgId: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, ...renderHook(() => useTray(orgId), { wrapper }) }
}

describe('useTray', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('reads GET /api/v1/me/tray when an org is active', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(TRAY_DATA))

    const { result } = renderUseTray('org-1')

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)).toContain(
      '/api/v1/me/tray',
    )
    expect(result.current.data?.[0].farm.name).toBe('Finca La Esperanza')
  })

  it('keys the query with the organization first [orgId, "tray"]', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(TRAY_DATA))

    const { queryClient } = renderUseTray('org-1')

    await waitFor(() => expect(queryClient.getQueryData(['org-1', 'tray'])).toBeDefined())
    // Negative assertion: 'tray' alone without org is not a key.
    expect(queryClient.getQueryData(['tray'])).toBeUndefined()
    expect(trayKey('org-1')).toEqual(['org-1', 'tray'])
  })

  it('opts the query into persistence with meta.persist', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(TRAY_DATA))

    const { queryClient } = renderUseTray('org-1')

    await waitFor(() => expect(queryClient.getQueryData(['org-1', 'tray'])).toBeDefined())
    const query = queryClient.getQueryCache().find({ queryKey: ['org-1', 'tray'] })
    expect(query?.meta?.persist).toBe(true)
    // Negative assertion: non-persisted flag is not set.
    expect(query?.meta?.persist).not.toBe(false)
  })

  it('never fetches without an organization', async () => {
    const { result } = renderUseTray(null)

    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    // Negative assertion: fetch is never called when orgId is null.
    expect(fetch).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })

  it('surfaces the server error on failure', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Internal Server Error', status: 500 }, 500),
    )

    const { result } = renderUseTray('org-1')

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })

  /**
   * A tray seeded for the org the user switched to must reach the phone exactly
   * like a fetched one, or the seeding only helps this session. The persister
   * writes what `persistOptions.dehydrateOptions.shouldDehydrateQuery` selects, and
   * that reads `meta.persist` off the Query's options — which for a query created by
   * `setQueryData` come from the defaults registered for its key.
   */
  it('persists a seeded tray entry like a fetched one, keeping the original fetch hour', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const fetchedAt = Date.parse('2026-09-30T15:00:00Z')
    queryClient.setQueryData(trayKey('org-1'), TRAY_DATA, { updatedAt: fetchedAt })

    seedTrayForOrg(queryClient, 'org-2', TRAY_DATA, fetchedAt)

    const seeded = queryClient.getQueryCache().find({ queryKey: trayKey('org-2') })
    expect(seeded?.meta?.persist).toBe(true)
    expect(seeded?.state.dataUpdatedAt).toBe(fetchedAt)
    // Negative assertion: the hour of a seeded entry is not "now" — the offline
    // banner must report when the server actually answered.
    expect(seeded?.state.dataUpdatedAt).not.toBe(Date.now())

    // The app's own persister filter picks the seeded entry up.
    const dehydrated = dehydrate(queryClient, persistOptions.dehydrateOptions)
    expect(dehydrated.queries?.map((query) => query.queryKey)).toContainEqual(['org-2', 'tray'])

    // Negative assertion: the org-1 entry is untouched, and a query that never
    // opted in is still left out of the persisted cache.
    expect(queryClient.getQueryState(trayKey('org-1'))?.dataUpdatedAt).toBe(fetchedAt)
    queryClient.setQueryData(['plots', 'farm-1'], [])
    expect(dehydrated.queries?.map((query) => query.queryKey)).not.toContainEqual([
      'plots',
      'farm-1',
    ])
  })

  it('seeds nothing for a tray it was never given', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    seedTrayForOrg(queryClient, 'org-2', null, 0)

    // Negative assertion: no cached tray, no invented entry — the screen keeps
    // waiting for the server.
    expect(queryClient.getQueryData(trayKey('org-2'))).toBeUndefined()
    expect(queryClient.getQueryCache().find({ queryKey: trayKey('org-2') })).toBeUndefined()
  })
})
