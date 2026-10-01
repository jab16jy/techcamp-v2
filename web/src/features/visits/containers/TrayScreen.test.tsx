/**
 * docs/07 §Mapa de pantallas "Bandeja del técnico" + D-T0.8, D-T0.9, D-T0.10, D-T6.1:
 * Technician tray screen tests.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as sessionApi from '../../../lib/api/session'
import * as activePlotApi from '../../../lib/api/activePlot'
import { trayKey } from '../api/useTray'
import { TrayScreen } from './TrayScreen'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const TRAY_ITEMS = [
  {
    farm: {
      id: 'farm-1',
      org_id: 'org-1',
      name: 'Finca La Palma',
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
        opened_at: '2026-09-30T10:00:00Z',
      },
      {
        id: 'alert-2',
        org_id: 'org-1',
        rule_id: 'rule-2',
        rule_code: 'soil_dry',
        plot_id: 'plot-1',
        node_id: null,
        state: 'open',
        severity: 'critical',
        opened_at: '2026-09-30T11:00:00Z',
      },
      {
        id: 'alert-3',
        org_id: 'org-1',
        rule_id: 'rule-3',
        rule_code: 'temp_high',
        plot_id: 'plot-1',
        node_id: null,
        state: 'open',
        severity: 'warning',
        opened_at: '2026-09-30T12:00:00Z',
      },
    ],
    last_visit_on: '2026-09-28',
  },
  {
    farm: {
      id: 'farm-2',
      org_id: 'org-2',
      name: 'Finca El Porvenir',
      municipality_code: '08002',
    },
    open_alerts: [],
    last_visit_on: null,
  },
]

const FARM_1_PLOTS = [
  { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Mango', area_ha: 2.5, irrigation_system: 'drip' },
]

const FARM_2_PLOTS = [
  { id: 'plot-2', farm_id: 'farm-2', name: 'Lote Yuca', area_ha: 1.0, irrigation_system: 'none' },
]

function renderScreen(initialEntries = ['/'], client?: QueryClient) {
  const queryClient = client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/', element: <TrayScreen /> },
      { path: '/estado', element: <p>Estado de la parcela</p> },
    ],
    { initialEntries },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { queryClient, router }
}

describe('TrayScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    sessionApi.setSession('token-tech', 'org-1')
  })

  afterEach(() => {
    sessionApi.clearSession()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps the server order of farms (asserts the whole list)', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Finca El Porvenir' })).toBeInTheDocument()

    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Finca La Palma', 'Finca El Porvenir'])
  })

  it('shows "Nunca visitada" when last_visit_on is null vs formatted date', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByText('2026-09-28')).toBeInTheDocument())
    expect(screen.getByText('Nunca visitada')).toBeInTheDocument()

    // Negative assertion: farm-1 does not say Nunca visitada
    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    expect(within(farm1Section).queryByText('Nunca visitada')).toBeNull()
    expect(within(farm1Section).getByText('2026-09-28')).toBeInTheDocument()

    // Negative assertion: farm-2 does not say 2026-09-28
    const farm2Section = screen.getByRole('region', { name: 'Finca El Porvenir' })
    expect(within(farm2Section).queryByText('2026-09-28')).toBeNull()
    expect(within(farm2Section).getByText('Nunca visitada')).toBeInTheDocument()
  })

  it('shows the open alert count and names the critical ones', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByText(/2 críticas/)).toBeInTheDocument())

    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    expect(within(farm1Section).getByText(/3 alertas abiertas/)).toBeInTheDocument()
    expect(within(farm1Section).getByText(/2 críticas/)).toBeInTheDocument()

    // Negative assertion: farm-2 with 0 alerts does not mention críticas
    const farm2Section = screen.getByRole('region', { name: 'Finca El Porvenir' })
    expect(within(farm2Section).getByText('Sin alertas abiertas')).toBeInTheDocument()
    expect(within(farm2Section).queryByText(/críticas/)).toBeNull()
  })

  it('renders EmptyState when tray is empty', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByText('Sin fincas asignadas')).toBeInTheDocument())
    // Negative assertion: no farm rows rendered
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
  })

  it('shows error state and allows retry', async () => {
    let attempts = 0
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) {
        attempts++
        if (attempts === 1) {
          return jsonResponse({ type: 'about:blank', title: 'Server Error', status: 500 }, 500)
        }
        return jsonResponse(TRAY_ITEMS)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByText('No se pudo cargar la bandeja')).toBeInTheDocument())
    // Negative assertion: farms not visible on error
    expect(screen.queryByText('Finca La Palma')).toBeNull()

    const retryButton = screen.getByRole('button', { name: 'Reintentar' })
    fireEvent.click(retryButton)

    await waitFor(() => expect(screen.getByText('Finca La Palma')).toBeInTheDocument())
  })

  it('shows offline banner with cached tray data when offline', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(trayKey('org-1'), TRAY_ITEMS)

    renderScreen(['/'], queryClient)

    await waitFor(() => expect(screen.getByText('Finca La Palma')).toBeInTheDocument())
    expect(screen.getByText(/Sin conexión/)).toBeInTheDocument()
  })

  it('keeps the persisted tray list and renders no error state when a refetch fails', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(trayKey('org-1'), TRAY_ITEMS)

    vi.mocked(fetch).mockRejectedValue(new Error('Network error'))

    renderScreen(['/'], queryClient)

    // Wait until the refetch actually fails and the query enters the error state
    await waitFor(() => {
      expect(queryClient.getQueryState(trayKey('org-1'))?.status).toBe('error')
    })

    // D-T0.10: The cached data is rendered, and no error EmptyState is shown
    expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Finca El Porvenir' })).toBeInTheDocument()
    // Negative assertion: does not render the error EmptyState
    expect(screen.queryByText('No se pudo cargar la bandeja')).toBeNull()
  })

  it('keeps a farm\'s cached plots when a refetch fails, and blocks with the error only when nothing is cached', async () => {
    let attempts = 0
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      if (url.includes('/api/v1/farms/farm-1/plots')) {
        attempts++
        // First load answers; every refetch after it fails.
        if (attempts === 1) return jsonResponse(FARM_1_PLOTS)
        throw new Error('Network error')
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const { queryClient } = renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument())
    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    fireEvent.click(within(farm1Section).getByRole('button', { name: /Finca La Palma/i }))
    await waitFor(() => expect(within(farm1Section).getByText('Lote Mango')).toBeInTheDocument())

    // A refetch that fails keeps the data and sets the error status.
    void queryClient.refetchQueries({ queryKey: ['plots', 'farm-1'] })
    await waitFor(() =>
      expect(queryClient.getQueryState(['plots', 'farm-1'])?.status).toBe('error'),
    )

    // D-T0.10: the cached plots are the offline answer, not a discarded list.
    expect(within(farm1Section).getByRole('button', { name: /Lote Mango/i })).toBeInTheDocument()
    // Negative assertion: no blocking error state over data that is still there.
    expect(
      within(farm1Section).queryByText('No se pudieron cargar las parcelas de esta finca.'),
    ).toBeNull()
    expect(within(farm1Section).queryByText('Cargando parcelas…')).toBeNull()
  })

  it('shows the blocking plots error and its retry when the first load fails (no data at all)', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      if (url.includes('/api/v1/farms/farm-1/plots')) throw new Error('Network error')
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument())
    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    fireEvent.click(within(farm1Section).getByRole('button', { name: /Finca La Palma/i }))

    await waitFor(() =>
      expect(
        within(farm1Section).getByText('No se pudieron cargar las parcelas de esta finca.'),
      ).toBeInTheDocument(),
    )
    expect(within(farm1Section).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument()
    // Negative assertion: no plot button is invented out of a failed load, and
    // the other farm's row is untouched.
    expect(within(farm1Section).queryByRole('button', { name: /Lote Mango/i })).toBeNull()
    expect(screen.getByRole('region', { name: 'Finca El Porvenir' })).toBeInTheDocument()
  })

  it('opens NewVisitSheet for the specific farm when clicking "Registrar visita"', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      if (url.includes('/api/v1/farms/farm-1/plots')) return jsonResponse(FARM_1_PLOTS)
      if (url.includes('/api/v1/me')) {
        return jsonResponse({
          id: 'user-tech-1',
          phone: '+573001234567',
          email: null,
          full_name: 'Técnico Agrónomo',
          memberships: [{ org_id: 'org-1', role: 'technician' }],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument())

    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    const addVisitBtn = within(farm1Section).getByRole('button', { name: 'Registrar visita' })
    fireEvent.click(addVisitBtn)

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Nueva visita de extensión' })).toBeInTheDocument())
    expect(screen.getByText('Registrar visita técnica a Finca La Palma.')).toBeInTheDocument()

    // Negative assertion: sheet is not for Finca El Porvenir
    expect(screen.queryByText('Registrar visita técnica a Finca El Porvenir.')).toBeNull()
  })

  it('tapping a plot of another org calls setOrgId, setActivePlotId and navigates to /estado', async () => {
    const setOrgIdSpy = vi.spyOn(sessionApi, 'setOrgId')
    const setActivePlotIdSpy = vi.spyOn(activePlotApi, 'setActivePlotId')

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      if (url.includes('/api/v1/farms/farm-2/plots')) return jsonResponse(FARM_2_PLOTS)
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca El Porvenir' })).toBeInTheDocument())

    // Open row for farm-2 (belongs to org-2, active is org-1)
    const farm2Section = screen.getByRole('region', { name: 'Finca El Porvenir' })
    const farm2Toggle = within(farm2Section).getByRole('button', { name: /Finca El Porvenir/i })
    fireEvent.click(farm2Toggle)

    await waitFor(() => expect(within(farm2Section).getByText('Lote Yuca')).toBeInTheDocument())

    const plotBtn = within(farm2Section).getByRole('button', { name: /Lote Yuca/i })
    fireEvent.click(plotBtn)

    // Calls setOrgId with org-2
    expect(setOrgIdSpy).toHaveBeenCalledWith('org-2')
    // Remembers plot for org-2
    expect(setActivePlotIdSpy).toHaveBeenCalledWith('org-2', 'plot-2')
    // Navigates to /estado
    await waitFor(() => expect(screen.getByText('Estado de la parcela')).toBeInTheDocument())
  })

  it('tapping a plot of the active org does NOT call setOrgId (negative assertion)', async () => {
    const setOrgIdSpy = vi.spyOn(sessionApi, 'setOrgId')
    const setActivePlotIdSpy = vi.spyOn(activePlotApi, 'setActivePlotId')

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/me/tray')) return jsonResponse(TRAY_ITEMS)
      if (url.includes('/api/v1/farms/farm-1/plots')) return jsonResponse(FARM_1_PLOTS)
      throw new Error(`unexpected request: ${url}`)
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Finca La Palma' })).toBeInTheDocument())

    // Farm-1 belongs to org-1 (which is the active org)
    const farm1Section = screen.getByRole('region', { name: 'Finca La Palma' })
    const farm1Toggle = within(farm1Section).getByRole('button', { name: /Finca La Palma/i })
    fireEvent.click(farm1Toggle)

    await waitFor(() => expect(within(farm1Section).getByText('Lote Mango')).toBeInTheDocument())

    const plotBtn = within(farm1Section).getByRole('button', { name: /Lote Mango/i })
    fireEvent.click(plotBtn)

    // Negative assertion: setOrgId was NOT called because org-1 is already active
    expect(setOrgIdSpy).not.toHaveBeenCalled()
    // Still sets active plot for org-1
    expect(setActivePlotIdSpy).toHaveBeenCalledWith('org-1', 'plot-1')
    // Navigates to /estado
    await waitFor(() => expect(screen.getByText('Estado de la parcela')).toBeInTheDocument())
  })
})
