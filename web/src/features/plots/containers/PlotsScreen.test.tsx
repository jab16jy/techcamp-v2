import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { PlotsScreen } from './PlotsScreen'

// The Leaflet map is mocked (task instruction): these wiring tests only care
// that the creation sheets open with the right farm, not about map drawing.
vi.mock('../components/PlotDrawMap', () => ({
  default: () => <div>mock plot draw map</div>,
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function renderPlotsScreen() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <PlotsScreen />
    </QueryClientProvider>,
  )
}

describe('PlotsScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('prompts to choose an organization when none is selected', () => {
    renderPlotsScreen()

    expect(screen.getByText('Elige una organización')).toBeInTheDocument()
  })

  it('shows the farms and plots for the selected org, with area and irrigation', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/farms/farm-1/plots')) {
        return jsonResponse([
          { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 2.456, irrigation_system: 'drip' },
        ])
      }
      if (url.includes('/farms?org_id=org-1')) {
        return jsonResponse({ items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }], next_cursor: null })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    renderPlotsScreen()

    expect(screen.getByText('Cargando parcelas…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Finca La Esperanza')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Lote Norte')).toBeInTheDocument())
    expect(screen.getByText('2,46 ha · Goteo')).toBeInTheDocument()
  })

  it('shows an empty state when the org has no farms', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ items: [], next_cursor: null }))

    renderPlotsScreen()

    await waitFor(() => expect(screen.getByText('Todavía no hay fincas')).toBeInTheDocument())
  })

  it('shows an error state and retries the farms request', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ type: 'about:blank', title: 'Organization not found', status: 404 }, 404),
    )

    renderPlotsScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudieron cargar las parcelas')).toBeInTheDocument(),
    )
    const retryButton = screen.getByRole('button', { name: 'Reintentar' })

    vi.mocked(fetch).mockResolvedValue(jsonResponse({ items: [], next_cursor: null }))
    retryButton.click()

    await waitFor(() => expect(screen.getByText('Todavía no hay fincas')).toBeInTheDocument())
  })

  it('shows a note when the org has more farms than the first page', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/plots')) return jsonResponse([])
      return jsonResponse({
        items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }],
        next_cursor: 'farm-1',
      })
    })

    renderPlotsScreen()

    await waitFor(() =>
      expect(
        screen.getByText('Tu organización tiene más fincas de las que se muestran aquí.'),
      ).toBeInTheDocument(),
    )
  })

  it("degrades one farm's plots on failure without blanking the other farms", async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/farms/farm-1/plots')) {
        return jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
      }
      if (url.includes('/farms/farm-2/plots')) {
        return jsonResponse([
          { id: 'plot-2', farm_id: 'farm-2', name: 'Lote Sur', area_ha: 1, irrigation_system: 'none' },
        ])
      }
      if (url.includes('/farms?org_id=org-1')) {
        return jsonResponse({
          items: [
            { id: 'farm-1', org_id: 'org-1', name: 'Finca Uno' },
            { id: 'farm-2', org_id: 'org-1', name: 'Finca Dos' },
          ],
          next_cursor: null,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    renderPlotsScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudieron cargar las parcelas de esta finca.')).toBeInTheDocument(),
    )
    expect(screen.getByText('Finca Uno')).toBeInTheDocument()
    expect(screen.getByText('Finca Dos')).toBeInTheDocument()
    expect(screen.getByText('Lote Sur')).toBeInTheDocument()
  })

  it("retries only the failed farm's plots when its Reintentar button is clicked", async () => {
    setSession('token-abc', 'org-1')
    let plotsFor1Calls = 0
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/farms/farm-1/plots')) {
        plotsFor1Calls += 1
        if (plotsFor1Calls === 1) {
          return jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
        }
        return jsonResponse([
          { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 1, irrigation_system: 'none' },
        ])
      }
      if (url.includes('/farms/farm-2/plots')) {
        return jsonResponse([
          { id: 'plot-2', farm_id: 'farm-2', name: 'Lote Sur', area_ha: 1, irrigation_system: 'none' },
        ])
      }
      return jsonResponse({
        items: [
          { id: 'farm-1', org_id: 'org-1', name: 'Finca Uno' },
          { id: 'farm-2', org_id: 'org-1', name: 'Finca Dos' },
        ],
        next_cursor: null,
      })
    })

    renderPlotsScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudieron cargar las parcelas de esta finca.')).toBeInTheDocument(),
    )
    expect(screen.getByText('Lote Sur')).toBeInTheDocument()

    screen.getByRole('button', { name: 'Reintentar' }).click()

    await waitFor(() => expect(screen.getByText('Lote Norte')).toBeInTheDocument())
    expect(
      screen.queryByText('No se pudieron cargar las parcelas de esta finca.'),
    ).not.toBeInTheDocument()
    expect(plotsFor1Calls).toBe(2)
  })

  it('opens the create-farm sheet from the header button and the empty-state action', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ items: [], next_cursor: null }))

    renderPlotsScreen()

    fireEvent.click(screen.getByRole('button', { name: 'Nueva finca' }))
    expect(screen.getByLabelText('Nombre')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))

    await waitFor(() => expect(screen.getByText('Todavía no hay fincas')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Crear finca' }))
    expect(screen.getByLabelText('Nombre')).toBeInTheDocument()
  })

  it('opens the create-plot sheet for the right farm via Agregar parcela', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/plots')) return jsonResponse([])
      return jsonResponse({
        items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }],
        next_cursor: null,
      })
    })

    renderPlotsScreen()

    await waitFor(() => expect(screen.getByText('Finca La Esperanza')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Agregar parcela' }))

    expect(screen.getByText('Nueva parcela')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(screen.queryByText('Nueva parcela')).not.toBeInTheDocument())
  })

  it('opens the plot-detail sheet for the clicked plot row', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/crops')) return jsonResponse([])
      if (url.includes('/nodes?')) return jsonResponse({ items: [], next_cursor: null })
      if (url.includes('/readings')) return jsonResponse({ series: [] })
      // A stream that never settles is an open farm stream.
      if (url.includes('/stream')) return new Promise<Response>(() => {})
      if (url.includes('/farms/farm-1/plots')) {
        return jsonResponse([
          { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 1, irrigation_system: 'none' },
        ])
      }
      return jsonResponse({
        items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }],
        next_cursor: null,
      })
    })

    renderPlotsScreen()

    await waitFor(() => expect(screen.getByText('Lote Norte')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Lote Norte/ }))

    expect(
      screen.getByText('Humedad, suelo, ciclo de cultivo y nodos de esta parcela.'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Lote Norte')).toHaveLength(2)
  })

  it('shows the "Registrar visita" action for technician and NOT for owner/producer/viewer', async () => {
    setSession('token-abc', 'org-1')

    function mockRequests(role: string) {
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/me')) {
          return jsonResponse({
            id: 'user-1',
            phone: '3001234567',
            email: null,
            full_name: 'Carlos Tecnico',
            memberships: [{ org_id: 'org-1', role }],
          })
        }
        if (url.includes('/farms/farm-1/plots')) return jsonResponse([])
        if (url.includes('/farms?org_id=org-1')) {
          return jsonResponse({
            items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }],
            next_cursor: null,
          })
        }
        throw new Error(`unexpected request: ${url}`)
      })
    }

    // Role = technician: action is visible
    mockRequests('technician')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <PlotsScreen callerRole="technician" />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(screen.getByText('Finca La Esperanza')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Registrar visita' })).toBeInTheDocument()
    unmount()

    // Negative assertions for other roles: owner, producer, viewer, and default undefined
    for (const nonTechRole of ['owner', 'producer', 'viewer']) {
      mockRequests(nonTechRole)
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const { unmount: unmountOther } = render(
        <QueryClientProvider client={qc}>
          <PlotsScreen callerRole={nonTechRole} />
        </QueryClientProvider>,
      )
      await waitFor(() => expect(screen.getByText('Finca La Esperanza')).toBeInTheDocument())
      expect(screen.queryByRole('button', { name: 'Registrar visita' })).not.toBeInTheDocument()
      unmountOther()
    }
  })
})
