/**
 * The home screen (docs/07 §Mapa de pantallas, "Inicio (pantalla más
 * importante)"), items 1–2: the active plot with its crop, and the decision
 * card with the irrigated and the rainfed variant (ADR-0023). T5b adds items
 * 3–5 to this same screen.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setActivePlotId } from '../../../lib/api/activePlot'
import { clearSession, setSession } from '../../../lib/api/session'
import { PlotStatusScreen } from './PlotStatusScreen'

const FARM = { id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }
const PLOTS = [
  { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 1.5, irrigation_system: 'drip' },
  { id: 'plot-2', farm_id: 'farm-1', name: 'Lote Sur', area_ha: 2, irrigation_system: 'none' },
]

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

interface StatusOverrides {
  active_cycle?: unknown
  recommendation?: unknown
}

function plotStatus(overrides: StatusOverrides = {}) {
  return {
    plot: {
      id: 'plot-1',
      org_id: 'org-1',
      farm_id: 'farm-1',
      name: 'Lote Norte',
      area_ha: 1.5,
      irrigation_system: 'drip',
    },
    active_cycle: { crop: { id: 1, code: 'maiz', name_es: 'Maíz' }, stage: 'development', day_of_cycle: 42 },
    latest: { soil_moisture_pct: 31.2, air_temp_c: 27, air_rh_pct: 80, at: '2026-09-30T12:00:00Z' },
    water_balance: null,
    recommendation: {
      kind: 'irrigate',
      depth_mm: 12,
      duration_min: 40,
      advice: [],
      rationale: { depletion_mm: 11, raw_mm: 14, et0_mm: 4.2 },
    },
    open_alerts: [],
    weather_next_3d: [],
    nodes: [],
    digital_adoption_index: null,
    ...overrides,
  }
}

/** Serves farms, plots and one `/status` per plot id. */
function mockRequests(statusByPlot: Record<string, unknown>, plots = PLOTS) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = requestUrl(input as Request)
    if (url.includes('/farms/farm-1/plots')) return jsonResponse(plots)
    if (url.includes('/farms?org_id=org-1')) {
      return jsonResponse({ items: [FARM], next_cursor: null })
    }
    for (const [plotId, status] of Object.entries(statusByPlot)) {
      if (url.includes(`/plots/${plotId}/status`)) return jsonResponse(status)
    }
    throw new Error(`unexpected request: ${url}`)
  })
}

function renderScreen() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/', element: <PlotStatusScreen /> },
      { path: '/parcelas', element: <p>Parcelas tab</p> },
    ],
    { initialEntries: ['/'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return queryClient
}

describe('PlotStatusScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('asks for an organization first when none is chosen, and fetches nothing', async () => {
    clearSession()

    renderScreen()

    expect(screen.getByText('Elige una organización')).toBeInTheDocument()
    await waitFor(() => expect(fetch).not.toHaveBeenCalled())
  })

  it('shows the first plot of the first farm as the active plot (D-T0.7)', async () => {
    mockRequests({ 'plot-1': plotStatus() })

    renderScreen()

    // Lote Norte is the first plot of the first farm, so the default is plot-1.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Maíz · día 42 · desarrollo')).toBeInTheDocument())
    expect(
      vi.mocked(fetch).mock.calls.some((call) =>
        requestUrl(call[0] as Request).includes('/plots/plot-1/status'),
      ),
    ).toBe(true)
    // Negative: it does not ask for a plot the user never opened.
    expect(
      vi.mocked(fetch).mock.calls.some((call) =>
        requestUrl(call[0] as Request).includes('/plots/plot-2/status'),
      ),
    ).toBe(false)
  })

  it('opens the plot the user opened last, not the first one (D-T0.7)', async () => {
    setActivePlotId('org-1', 'plot-2')
    mockRequests({ 'plot-2': plotStatus({ active_cycle: null }) })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Sur' })).toBeInTheDocument())
    // Negative: the remembered plot is the only one asked for; plot-1 is not.
    const statusCalls = vi
      .mocked(fetch)
      .mock.calls.map((call) => requestUrl(call[0] as Request))
      .filter((url) => url.includes('/status'))
    expect(statusCalls).toHaveLength(1)
    expect(statusCalls[0]).toContain('/plots/plot-2/status')
    expect(statusCalls[0]).not.toContain('plot-1')
  })

  it('falls back to the first plot when the remembered one no longer exists', async () => {
    setActivePlotId('org-1', 'plot-deleted')
    mockRequests({ 'plot-1': plotStatus() })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument())
    // Negative: a deleted plot is never requested, and never named on screen.
    expect(
      vi.mocked(fetch).mock.calls.some((call) =>
        requestUrl(call[0] as Request).includes('plot-deleted'),
      ),
    ).toBe(false)
    expect(screen.queryByText(/plot-deleted/)).not.toBeInTheDocument()
  })

  it('leads to Parcelas when the organization has no plots', async () => {
    mockRequests({}, [])

    renderScreen()

    await waitFor(() => expect(screen.getByText('Sin parcelas')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('link', { name: 'Ir a Parcelas' }))

    expect(await screen.findByText('Parcelas tab')).toBeInTheDocument()
    // Negative: no status request is made without a plot to ask for.
    expect(
      vi.mocked(fetch).mock.calls.some((call) => requestUrl(call[0] as Request).includes('/status')),
    ).toBe(false)
  })

  it('answers with the irrigated decision and its why, one tap away (docs/07 item 2)', async () => {
    mockRequests({ 'plot-1': plotStatus() })

    renderScreen()

    await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
    expect(screen.getByText('Regar')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Hoy: regar 12 mm/ }))

    expect(await screen.findByText('Por qué: Regar')).toBeInTheDocument()
    expect(screen.getAllByText(/11 mm/).length).toBeGreaterThan(0)
  })

  it('says the plot needs no water today, with no depth to show', async () => {
    mockRequests({
      'plot-1': plotStatus({
        recommendation: {
          kind: 'not_needed',
          depth_mm: null,
          duration_min: null,
          advice: [],
          rationale: { depletion_mm: 2, raw_mm: 9 },
        },
      }),
    })

    renderScreen()

    await waitFor(() => expect(screen.getByText('Hoy no necesita riego')).toBeInTheDocument())
    expect(screen.getByText('Bien')).toBeInTheDocument()
    // Negative: no depth, no minutes, no band claiming to irrigate.
    expect(screen.queryByText(/regar \d/)).not.toBeInTheDocument()
  })

  it('admits a plot with no recommendation today instead of inventing one', async () => {
    mockRequests({ 'plot-1': plotStatus({ recommendation: null }) })

    renderScreen()

    await waitFor(() => expect(screen.getByText('Aún no hay recomendación para hoy')).toBeInTheDocument())
    // Negative: nothing is claimed about water today.
    expect(screen.queryByText(/regar \d/)).not.toBeInTheDocument()
    expect(screen.queryByText(/mm/)).not.toBeInTheDocument()
  })

  it('shows the rainfed card: deficit, 7-day rain and the advice, never a depth', async () => {
    mockRequests({
      'plot-2': {
        ...plotStatus(),
        plot: { ...plotStatus().plot, id: 'plot-2', name: 'Lote Sur', irrigation_system: 'none' },
        recommendation: {
          kind: 'rainfed',
          depth_mm: null,
          duration_min: null,
          advice: ['conserve_moisture'],
          rationale: {
            depletion_mm: 80,
            raw_mm: 60,
            forecast_rain_7d_mm: 12,
            forecast_et0_7d_mm: 20,
          },
        },
      },
    })
    setActivePlotId('org-1', 'plot-2')

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Sur' })).toBeInTheDocument())
    await waitFor(() =>
      expect(screen.getByText('Al cultivo le faltan 80 mm: está en estrés')).toBeInTheDocument(),
    )
    expect(screen.getByText('Se espera 12 mm de lluvia en los próximos 7 días.')).toBeInTheDocument()
    expect(screen.getByText('Cubra el suelo con rastrojo para conservar la humedad.')).toBeInTheDocument()
    // ADR-0023: a rainfed plot never shows an irrigation depth nor minutes.
    expect(screen.getByText('Estrés')).toBeInTheDocument()
    expect(screen.queryByText(/regar \d/)).not.toBeInTheDocument()
    expect(screen.queryByText(/≈/)).not.toBeInTheDocument()
  })

  it('gives each rainfed advice code its own sentence of the day', async () => {
    const adviceSentences: Record<string, string> = {
      delay_sowing: 'Aplaza la siembra: se espera menos lluvia que la que el cultivo necesita.',
      rain_expected: 'Espera la lluvia antes de intervenir: se espera que cubra el déficit.',
      conserve_moisture: 'Cubra el suelo con rastrojo para conservar la humedad.',
      prioritize_harvest: 'Prioriza la cosecha: el cultivo ya está en etapa final.',
      no_action: 'Por hoy no hace falta ninguna acción en esta parcela.',
    }

    for (const [code, sentence] of Object.entries(adviceSentences)) {
      setActivePlotId('org-1', 'plot-2')
      mockRequests({
        'plot-2': {
          ...plotStatus(),
          plot: { ...plotStatus().plot, id: 'plot-2', name: 'Lote Sur', irrigation_system: 'none' },
          recommendation: {
            kind: 'rainfed',
            depth_mm: null,
            duration_min: null,
            advice: [code],
            rationale: { depletion_mm: 80, raw_mm: 60, forecast_rain_7d_mm: 12 },
          },
        },
      })
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const router = createMemoryRouter([{ path: '/', element: <PlotStatusScreen /> }], {
        initialEntries: ['/'],
      })
      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      )

      await waitFor(() => expect(screen.getByText(sentence)).toBeInTheDocument())
      // Negative: one advice code shows one sentence — never a depth or a duration.
      expect(screen.queryByText(/regar \d/)).not.toBeInTheDocument()
      unmount()
    }
  })

  it('shows the plot name alone when there is no active cycle', async () => {
    mockRequests({ 'plot-1': plotStatus({ active_cycle: null }) })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument())
    // The status has landed: only then does "no cycle line" mean anything.
    await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
    // Negative: no crop, no "día", no stage invented for a plot with no cycle.
    expect(screen.queryByText(/día/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Maíz/)).not.toBeInTheDocument()
  })

  it('shows no cycle day when the sowing is later than today (#205)', async () => {
    mockRequests({
      'plot-1': plotStatus({ active_cycle: { crop: { id: 1, code: 'maiz', name_es: 'Maíz' }, stage: null, day_of_cycle: null } }),
    })

    renderScreen()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument())
    // The crop is the whole cycle line here: the status has landed.
    await waitFor(() => expect(screen.getByText('Maíz')).toBeInTheDocument())
    // Negative: never "día 0" and never a negative day.
    expect(screen.queryByText(/día 0/)).not.toBeInTheDocument()
    expect(screen.queryByText(/día -/)).not.toBeInTheDocument()
    expect(screen.queryByText(/desarrollo/)).not.toBeInTheDocument()
  })

  it('shows a failure state and retries the status request', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/plots/plot-1/status')) {
        return jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
      }
      if (url.includes('/farms/farm-1/plots')) return jsonResponse(PLOTS)
      return jsonResponse({ items: [FARM], next_cursor: null })
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudo cargar el estado de la parcela')).toBeInTheDocument(),
    )
    // Negative: no decision is shown over a failed request.
    expect(screen.queryByText(/Hoy:/)).not.toBeInTheDocument()

    mockRequests({ 'plot-1': plotStatus() })
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))

    await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
  })
})
