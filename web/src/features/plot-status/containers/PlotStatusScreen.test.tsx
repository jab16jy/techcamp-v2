/**
 * The home screen (docs/07 §Mapa de pantallas, "Inicio (pantalla más
 * importante)"), items 1–2: the active plot with its crop, and the decision
 * card with the irrigated and the rainfed variant (ADR-0023). T5b adds items
 * 3–5 to this same screen.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getActivePlotId, setActivePlotId } from '../../../lib/api/activePlot'
import { todayInBogota } from '../../../lib/date'
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

/** An ISO timestamp `minutes` in the past, so a freshness clause is
 * deterministic without fake timers (which stall every `waitFor` after them). */
function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString()
}

/** The Bogotá day after today, so the "Mañana" label is deterministic. */
function tomorrowInBogota(): string {
  const date = new Date(`${todayInBogota()}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
}

/** Any part of the payload a test wants to replace, except the plot's identity. */
type StatusOverrides = Record<string, unknown>

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

/** Serves farms, plots and one `/status` per plot id. The farm stream is a
 * never-settling open connection, as it is in the app: once a status resolves
 * the screen subscribes to `GET /stream?farm_id=`, and a mock that throws there
 * sends `useFarmEvents` into a reconnect loop that outlives the test. */
function mockRequests(statusByPlot: Record<string, unknown>, plots = PLOTS) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = requestUrl(input as Request)
    if (url.includes('/farms/farm-1/plots')) return jsonResponse(plots)
    if (url.includes('/farms?org_id=org-1')) {
      return jsonResponse({ items: [FARM], next_cursor: null })
    }
    if (url.includes('/stream')) return new Promise<Response>(() => {})
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
    // The offline case spies on `navigator.onLine`; `defineProperty` would
    // shadow jsdom's prototype getter for the rest of the file and wedge every
    // later render, so the spy is restored here instead.
    vi.restoreAllMocks()
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
    mockRequests({
      'plot-2': {
        ...plotStatus(),
        plot: { ...plotStatus().plot, id: 'plot-2', name: 'Lote Sur' },
        active_cycle: null,
      },
    })

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

  it('falls back to the first plot when the remembered one is not in the org', async () => {
    setActivePlotId('org-1', 'plot-deleted')
    mockRequests({ 'plot-1': plotStatus() })

    renderScreen()

    // The remembered id is asked for first — a plot the user really did open —
    // and the lists that arrive without it drop the choice for the default.
    await waitFor(() => expect(getActivePlotId('org-1')).toBe('plot-1'))
    await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
    // Negative: the stale id is never left as the active plot.
    expect(getActivePlotId('org-1')).not.toBe('plot-deleted')
    expect(screen.queryByText(/Sin parcelas/)).not.toBeInTheDocument()
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
    // Negative: no depth, no minutes, no band claiming to irrigate.
    expect(screen.queryByText(/regar \d/)).not.toBeInTheDocument()
    expect(screen.queryByText('Regar')).not.toBeInTheDocument()
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
      if (url.includes('/stream')) return new Promise<Response>(() => {})
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

  describe('item 3 — open alerts', () => {
    const withAlerts = (open_alerts: unknown[]) =>
      mockRequests({ 'plot-1': plotStatus({ open_alerts }) })

    it('lists the open alerts in the order the server sends them, critical first', async () => {
      withAlerts([
        {
          id: 'a-critical',
          rule_code: 'flood_risk',
          severity: 'critical',
          state: 'open',
          opened_at: '2026-09-30T12:00:00Z',
          evidence: {},
        },
        {
          id: 'a-warning',
          rule_code: 'water_stress',
          severity: 'warning',
          state: 'open',
          opened_at: '2026-09-30T11:00:00Z',
          evidence: {},
        },
      ])

      renderScreen()

      await waitFor(() => expect(screen.getByText('Riesgo de inundación')).toBeInTheDocument())
      expect(screen.getByText('Estrés hídrico')).toBeInTheDocument()
      expect(screen.getByText('Crítico')).toBeInTheDocument()
      // The server's order is the documented order (critical, then newest).
      const titles = screen
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
      expect(titles.indexOf('Riesgo de inundación')).toBeLessThan(titles.indexOf('Estrés hídrico'))
    })

    it('says a plot with no open alerts in one quiet line, with no card', async () => {
      withAlerts([])

      renderScreen()

      await waitFor(() => expect(screen.getByText('Sin alertas abiertas.')).toBeInTheDocument())
      // Negative: nothing loud — no severity badge at all.
      expect(screen.queryByText('Crítico')).not.toBeInTheDocument()
      expect(screen.queryByText('Advertencia')).not.toBeInTheDocument()
      expect(screen.queryByText('Información')).not.toBeInTheDocument()
    })
  })

  describe('item 4 — soil moisture and 3-day forecast', () => {
    it('shows the reading with its age, and the three forecast days', async () => {
      mockRequests({
        'plot-1': plotStatus({
          latest: {
            soil_moisture_pct: 31.2,
            air_temp_c: 27,
            air_rh_pct: 80,
            at: minutesAgo(30),
          },
          weather_next_3d: [
            {
              day: todayInBogota(),
              is_forecast: true,
              et0_mm: 4,
              rain_mm: 12,
              tmin_c: 24,
              tmax_c: 31,
              rh_mean_pct: 70,
              fetched_at: '2026-09-30T06:00:00Z',
              stale: false,
            },
            {
              day: tomorrowInBogota(),
              is_forecast: true,
              et0_mm: 4,
              rain_mm: null,
              tmin_c: 24,
              tmax_c: null,
              rh_mean_pct: 70,
              fetched_at: '2026-09-30T06:00:00Z',
              stale: true,
            },
          ],
        }),
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('31,2 %')).toBeInTheDocument())
      expect(screen.getAllByText('dato de hace 30 min').length).toBeGreaterThan(0)
      expect(screen.getByText('Hoy')).toBeInTheDocument()
      expect(screen.getByText('Mañana')).toBeInTheDocument()
      // The stale flag is shown; a missing rain is a dash, not 0 mm.
      expect(screen.getByText('dato viejo')).toBeInTheDocument()
      expect(screen.getByText('12 mm')).toBeInTheDocument()
      expect(screen.getByText('—')).toBeInTheDocument()
      expect(screen.getByText('24–31 °C')).toBeInTheDocument()
      expect(screen.getByText('24 °C')).toBeInTheDocument()
    })

    it('says there is no reading instead of showing a zero, and hides the empty forecast', async () => {
      mockRequests({
        'plot-1': plotStatus({
          latest: { soil_moisture_pct: null, air_temp_c: null, air_rh_pct: null, at: null },
          weather_next_3d: [],
        }),
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Sin lectura')).toBeInTheDocument())
      // Negative: no 0 %, and no freshness claim over a reading that never arrived.
      expect(screen.queryByText('0 %')).not.toBeInTheDocument()
      expect(screen.getAllByText('sin datos').length).toBeGreaterThan(0)
      expect(screen.queryByText('Hoy')).not.toBeInTheDocument()
    })
  })

  describe('item 5 — sync and nodes', () => {
    it('shows the connection line and every node with its state and last contact', async () => {
      mockRequests({
        'plot-1': plotStatus({
          nodes: [
            {
              node_id: 'node-1',
              status: 'online',
              last_seen_at: '2026-09-30T12:00:00Z',
              completeness_24h: 0.198,
            },
            {
              node_id: 'node-2',
              status: 'offline',
              last_seen_at: '2026-09-30T09:00:00Z',
              completeness_24h: null,
            },
          ],
        }),
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('En línea')).toBeInTheDocument())
      expect(screen.getByText('Sin señal')).toBeInTheDocument()
      // `formatPercent` (design system) formats with toFixed, so this reads "19.8 %".
      expect(screen.getByText('19.8 %')).toBeInTheDocument()
      // Negative: a node that has not reported has a dash, not 0 %.
      expect(screen.queryByText('0,0 %')).not.toBeInTheDocument()
      // Negative: online, so no connection banner and no "Sin conexión" anywhere.
      expect(screen.queryByText(/Sin conexión/)).not.toBeInTheDocument()
    })

    it('says the plot has no nodes rather than showing an empty section', async () => {
      mockRequests({ 'plot-1': plotStatus({ nodes: [] }) })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Esta parcela no tiene nodos.')).toBeInTheDocument())
      expect(screen.queryByText('En línea')).not.toBeInTheDocument()
    })
  })

  describe('SSE invalidation (docs/07 §Flujo de datos y offline)', () => {
    it('refetches the status when the farm stream reports a reading', async () => {
      let pushEvent: ((chunk: string) => void) | null = null
      const statusCalls = () =>
        vi
          .mocked(fetch)
          .mock.calls.map((call) => requestUrl(call[0] as Request))
          .filter((url) => url.includes('/plots/plot-1/status')).length

      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/stream')) {
          return new Promise<Response>((resolve) => {
            const encoder = new TextEncoder()
            resolve(
              new Response(
                new ReadableStream({
                  start(controller) {
                    pushEvent = (chunk) => controller.enqueue(encoder.encode(chunk))
                  },
                }),
                { status: 200, headers: { 'content-type': 'text/event-stream' } },
              ),
            )
          })
        }
        if (url.includes('/plots/plot-1/status')) return jsonResponse(plotStatus())
        if (url.includes('/farms/farm-1/plots')) return jsonResponse(PLOTS)
        return jsonResponse({ items: [FARM], next_cursor: null })
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      const before = statusCalls()

      await act(async () => {
        pushEvent?.(
          'id: 1\nevent: reading\ndata: {"plot_id":"plot-1","metric":"soil_moisture","value":12,"at":"2026-09-30T12:00:00Z"}\n\n',
        )
      })

      await waitFor(() => expect(statusCalls()).toBeGreaterThan(before))
    })

    it('does not refetch the status for an event from another plot', async () => {
      let pushEvent: ((chunk: string) => void) | null = null
      const statusCalls = () =>
        vi
          .mocked(fetch)
          .mock.calls.map((call) => requestUrl(call[0] as Request))
          .filter((url) => url.includes('/plots/plot-1/status')).length

      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/stream')) {
          return new Promise<Response>((resolve) => {
            const encoder = new TextEncoder()
            resolve(
              new Response(
                new ReadableStream({
                  start(controller) {
                    pushEvent = (chunk) => controller.enqueue(encoder.encode(chunk))
                  },
                }),
                { status: 200, headers: { 'content-type': 'text/event-stream' } },
              ),
            )
          })
        }
        if (url.includes('/plots/plot-1/status')) return jsonResponse(plotStatus())
        if (url.includes('/farms/farm-1/plots')) return jsonResponse(PLOTS)
        return jsonResponse({ items: [FARM], next_cursor: null })
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      const before = statusCalls()

      await act(async () => {
        pushEvent?.(
          'id: 2\nevent: reading\ndata: {"plot_id":"plot-9","metric":"soil_moisture","value":12,"at":"2026-09-30T12:00:00Z"}\n\n',
        )
      })

      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(statusCalls()).toBe(before)
    })
  })

  describe('offline (D-T0.10: the last state with its time)', () => {
    it('opens from the persisted cache with no connection, and says how old it is', async () => {
      // A remembered plot and its status already in the query cache: the phone
      // reopens with no network, and the farms/plots lists never answer.
      setActivePlotId('org-1', 'plot-1')
      const cached = plotStatus({
        latest: {
          soil_moisture_pct: 31.2,
          air_temp_c: 27,
          air_rh_pct: 80,
          at: minutesAgo(30),
        },
      })
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/plots/plot-1/status')) {
          throw new TypeError('Failed to fetch')
        }
        if (url.includes('/farms/farm-1/plots')) {
          throw new TypeError('Failed to fetch')
        }
        if (url.includes('/farms?org_id=org-1')) {
          throw new TypeError('Failed to fetch')
        }
        if (url.includes('/stream')) return new Promise<Response>(() => {})
        throw new Error(`unexpected request: ${url}`)
      })

      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      queryClient.setQueryData(['org-1', 'status', 'plot-1'], cached)
      const router = createMemoryRouter([{ path: '/', element: <PlotStatusScreen /> }], {
        initialEntries: ['/'],
      })
      // Before the render: `useOnlineStatus` reads this in its first snapshot.
      vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
      render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      )

      // The persisted state is on screen, not a loading line that never ends.
      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      expect(screen.getAllByText(/Sin conexión/).length).toBeGreaterThan(0)
      expect(screen.getAllByText(/dato de hace 30 min/).length).toBeGreaterThan(0)
      // Negative: a failed refetch over cached data does not replace it with an error.
      expect(
        screen.queryByText('No se pudo cargar el estado de la parcela'),
      ).not.toBeInTheDocument()
    })

    it('resolves the remembered plot without waiting for the lists, which never load', async () => {
      setActivePlotId('org-1', 'plot-1')
      // The lists hang: no farms answer, so a resolution that waits for them
      // would leave the home on "Cargando parcelas…" forever.
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/stream')) return new Promise<Response>(() => {})
        if (url.includes('/farms/farm-1/plots') || url.includes('/farms?org_id=org-1')) {
          return new Promise<Response>(() => {})
        }
        if (url.includes('/plots/plot-1/status')) return jsonResponse(plotStatus())
        throw new Error(`unexpected request: ${url}`)
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      // Negative: the remembered plot is not abandoned for a loading line.
      expect(screen.queryByText('Cargando parcelas…')).not.toBeInTheDocument()
    })

    it('answers for a farm that loaded when another farm fails (R3, #21 round 10)', async () => {
      // Farm 1 answers, farm 2 does not. The degradation `usePlotsByFarm` exists
      // for must reach the home too: one farm's failure is not a blank home.
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = requestUrl(input as Request)
        if (url.includes('/stream')) return new Promise<Response>(() => {})
        if (url.includes('/farms/farm-2/plots')) {
          return jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
        }
        if (url.includes('/farms/farm-1/plots')) {
          return jsonResponse([
            { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 1.5, irrigation_system: 'drip' },
          ])
        }
        if (url.includes('/plots/plot-1/status')) return jsonResponse(plotStatus())
        return jsonResponse({
          items: [
            { id: 'farm-1', org_id: 'org-1', name: 'Finca Uno' },
            { id: 'farm-2', org_id: 'org-1', name: 'Finca Dos' },
          ],
          next_cursor: null,
        })
      })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument()
      // Negative: the failed farm does not become a whole-screen error, and the
      // farm that answered is not discarded.
      expect(
        screen.queryByText('No se pudieron cargar las parcelas'),
      ).not.toBeInTheDocument()
    })

    it('remembers the default plot, so the next offline open has one to ask for', async () => {
      mockRequests({ 'plot-1': plotStatus() })

      renderScreen()

      await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
      expect(getActivePlotId('org-1')).toBe('plot-1')
    })

    it('forgets a remembered plot the server no longer knows, and answers for the default', async () => {
      setActivePlotId('org-1', 'plot-deleted')
      mockRequests({ 'plot-1': plotStatus() })

      renderScreen()

      // The remembered id is requested first, 404s, and is dropped for the default.
      await waitFor(() => expect(getActivePlotId('org-1')).toBe('plot-1'))
      expect(
        vi.mocked(fetch).mock.calls.some((call) =>
          requestUrl(call[0] as Request).includes('/plots/plot-deleted/status'),
        ),
      ).toBe(true)
    })
  })

  it('renders nothing for the adoption index, which stays null until E11 (D-T0.2)', async () => {
    mockRequests({ 'plot-1': plotStatus({ digital_adoption_index: null }) })

    renderScreen()

    await waitFor(() => expect(screen.getByText('Hoy: regar 12 mm (≈ 40 min)')).toBeInTheDocument())
    // Negative: nothing about adoption is rendered, and the only percentage on
    // screen is the soil reading this payload really carries.
    expect(screen.queryByText(/adop/i)).not.toBeInTheDocument()
    expect(screen.getByText('31,2 %')).toBeInTheDocument()
    expect(screen.getAllByText(/%$/)).toHaveLength(1)
  })
})
