import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { PlotDetailSheet } from './PlotDetailSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const CROPS = [
  {
    id: 1,
    code: 'maize',
    name_es: 'Maíz',
    kc_source: 'fao56',
    stages: [
      { stage: 'initial', length_days: 20, kc: 0.3, depletion_fraction_p: 0.55 },
      { stage: 'development', length_days: 35, kc: 0.7, depletion_fraction_p: 0.55 },
      { stage: 'mid', length_days: 40, kc: 1.2, depletion_fraction_p: 0.55 },
      { stage: 'late', length_days: 30, kc: 0.6, depletion_fraction_p: 0.55 },
    ],
  },
  { id: 2, code: 'yam', name_es: 'Ñame', kc_source: 'none', stages: [] },
]

const NODE = {
  id: 'node-1',
  org_id: 'org-1',
  plot_id: 'plot-1',
  transport: 'wifi',
  dev_eui: '70B3D5A3B0000001',
  firmware: '1.0.0',
  interval_s: 300,
  claimed_at: '2026-09-01T12:00:00Z',
  last_seen_at: '2026-09-25T10:00:00Z',
  status: 'online',
}

function renderSheet(onOpenChange: (open: boolean) => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <PlotDetailSheet
        open
        onOpenChange={onOpenChange}
        plotId="plot-1"
        farmId="farm-1"
        plotName="Lote Norte"
      />
    </QueryClientProvider>,
  )
}

type Route = () => Response | Promise<Response>

/** The nodes and soil-moisture sections ask on every render and the stream is a
 * long-lived request, so those three are defaulted instead of repeated in every test. */
const DEFAULT_ROUTES: Record<string, Route> = {
  '/nodes?': () => jsonResponse({ items: [], next_cursor: null }),
  '/readings': () => jsonResponse({ series: [] }),
  // A stream that never settles is an open farm stream.
  '/stream': () => new Promise<Response>(() => {}),
}

/** Mocks `GET /crops` plus every other call the test provides by URL substring. A test's
 * own route is looked up before the defaults, so it can override any of them. */
function mockFetch(byUrl: Record<string, Route>) {
  const routes = [...Object.entries(byUrl), ...Object.entries(DEFAULT_ROUTES)]
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = requestUrl(input as Request)
    const match = routes.find(([substr]) => url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${url}`)
    return match[1]()
  })
}

/** A farm stream that emits the given raw SSE text and then stays open. */
function streamResponse(chunk: string): Promise<Response> {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(chunk))
    },
  })
  return Promise.resolve(
    new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
  )
}

describe('PlotDetailSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('shows no soil data until an autofill or manual save happens this session', async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    expect(screen.getByText('Todavía no hay datos de suelo en esta sesión.')).toBeInTheDocument()
  })

  it('lists the plot nodes with their health in the Nodos section', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/nodes?': () => jsonResponse({ items: [NODE], next_cursor: null }),
      '/health': () =>
        jsonResponse({
          last_seen_at: null,
          battery_v: 3.9,
          rssi: -78,
          completeness_24h: 0.198,
        }),
    })
    renderSheet()

    expect(screen.getByRole('heading', { name: 'Nodos' })).toBeInTheDocument()
    expect(await screen.findByText('70B3D5A3B0000001')).toBeInTheDocument()
    expect(await screen.findByText('19.8 %')).toBeInTheDocument()
  })

  it('autofills the soil profile from SoilGrids', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'soil:autofill': () =>
        jsonResponse({
          plot_id: 'plot-1',
          source: 'soilgrids',
          ph: 6.5,
          organic_matter_pct: 3.2,
          texture: 'loam',
          field_capacity_pct: 25,
          wilting_point_pct: 12,
          root_depth_cm: null,
        }),
    })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Autocompletar desde SoilGrids' }))

    await waitFor(() => expect(screen.getByText('SoilGrids')).toBeInTheDocument())
    expect(screen.getByText('Franco')).toBeInTheDocument()
    expect(screen.getByText('6.5')).toBeInTheDocument()
  })

  it('shows a clear message when SoilGrids is unavailable (502/503)', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'soil:autofill': () =>
        jsonResponse(
          { type: 'about:blank', title: 'SoilGrids is unavailable', status: 503 },
          503,
        ),
    })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Autocompletar desde SoilGrids' }))

    await waitFor(() =>
      expect(
        screen.getByText('SoilGrids no está disponible en este momento. Intenta de nuevo más tarde.'),
      ).toBeInTheDocument(),
    )
  })

  it('saves a manually edited soil profile and shows the result', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/soil': () =>
        jsonResponse({
          plot_id: 'plot-1',
          source: 'lab',
          ph: 6,
          organic_matter_pct: 2,
          texture: 'clay',
          field_capacity_pct: 36,
          wilting_point_pct: 22,
          root_depth_cm: 60,
        }),
    })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Editar manualmente' }))
    fireEvent.change(screen.getByLabelText(/pH/), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText(/Materia orgánica/), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText(/Profundidad radicular/), { target: { value: '60' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar suelo' }))

    await waitFor(() => expect(screen.getByText('Laboratorio')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Guardar suelo' })).not.toBeInTheDocument()

    const [request] = vi.mocked(fetch).mock.calls.find(([input]) =>
      requestUrl(input as Request).includes('/soil'),
    )!
    const body = await requestOf(request as Request).json()
    expect(body).toEqual({
      texture: null,
      ph: 6,
      organic_matter_pct: 2,
      field_capacity_pct: null,
      wilting_point_pct: null,
      root_depth_cm: 60,
    })
  })

  it("shows the 422 detail on the soil form", async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/soil': () =>
        jsonResponse(
          { type: 'about:blank', title: 'Validation error', status: 422, detail: 'ph: out of range' },
          422,
        ),
    })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Editar manualmente' }))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar suelo' }))

    await waitFor(() => expect(screen.getByText('ph: out of range')).toBeInTheDocument())
  })

  it("shows the crop's Kc by stage once a crop is selected", async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))

    expect(
      screen.getByText('Kc (FAO-56 (directo)): Inicial 0.3 · Desarrollo 0.7 · Media 1.2 · Final 0.6'),
    ).toBeInTheDocument()
  })

  it('disables the SoilGrids autofill button while manual soil editing is open', async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    const autofillButton = screen.getByRole('button', { name: 'Autocompletar desde SoilGrids' })
    const editButton = screen.getByRole('button', { name: 'Editar manualmente' })
    expect(autofillButton).toBeEnabled()

    fireEvent.click(editButton)
    expect(autofillButton).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancelar edición' }))
    expect(autofillButton).toBeEnabled()
  })

  it('shows general error copy for 502/503 on manual soil PUT, not SoilGrids copy', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/soil': () =>
        jsonResponse(
          { type: 'about:blank', title: 'Bad Gateway', status: 502 },
          502,
        ),
    })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Editar manualmente' }))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar suelo' }))

    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
    expect(
      screen.queryByText(/SoilGrids no está disponible/),
    ).not.toBeInTheDocument()
  })

  it('shows general error copy for 502/503 on cycle POST, not SoilGrids copy', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'plots/plot-1/cycles': () =>
        jsonResponse(
          { type: 'about:blank', title: 'Bad Gateway', status: 502 },
          502,
        ),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
    expect(
      screen.queryByText(/SoilGrids no está disponible/),
    ).not.toBeInTheDocument()
  })

  it('shows general error copy for 502/503 on cycle PATCH, not SoilGrids copy', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'cycles/cycle-1': () =>
        jsonResponse(
          { type: 'about:blank', title: 'Service Unavailable', status: 503 },
          503,
        ),
      '/plots/plot-1/cycles': () =>
        jsonResponse(
          {
            id: 'cycle-1',
            plot_id: 'plot-1',
            crop_id: 1,
            sown_on: '2026-06-01',
            expected_harvest_on: '2026-09-15',
            status: 'active',
          },
          201,
        ),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() => expect(screen.getByText('Estado: Activo')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Registrar cosecha' }))

    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
    expect(
      screen.queryByText(/SoilGrids no está disponible/),
    ).not.toBeInTheDocument()
  })

  it('shows loading state while crops are loading', async () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}))
    renderSheet()

    expect(screen.getByText('Cargando cultivos…')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('shows error state with retry when crops request fails', async () => {
    let attempts = 0
    mockFetch({
      '/crops': () => {
        attempts += 1
        if (attempts === 1) {
          return jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
        }
        return jsonResponse(CROPS)
      },
    })
    renderSheet()

    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
    const retryButton = screen.getByRole('button', { name: 'Reintentar' })
    expect(retryButton).toBeInTheDocument()

    fireEvent.click(retryButton)
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    expect(attempts).toBe(2)
  })

  it('disables starting a cycle until crop and sowing date are selected', async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    const submitButton = screen.getByRole('button', { name: 'Iniciar ciclo' })
    expect(submitButton).toBeDisabled()

    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    expect(submitButton).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    expect(submitButton).toBeEnabled()
  })

  it('starts a cycle with crop and sowing date only, letting backend derive expected harvest', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'plots/plot-1/cycles': () =>
        jsonResponse(
          {
            id: 'cycle-1',
            plot_id: 'plot-1',
            crop_id: 1,
            sown_on: '2026-06-01',
            expected_harvest_on: '2026-09-15',
            status: 'active',
          },
          201,
        ),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    expect(screen.queryByLabelText(/Cosecha esperada/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() =>
      expect(screen.getByText(/cosecha esperada el 2026-09-15/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/Maíz · sembrado el 2026-06-01/)).toBeInTheDocument()

    const cycleCalls = vi.mocked(fetch).mock.calls.filter(([input]) =>
      requestUrl(input as Request).includes('/cycles'),
    )
    expect(cycleCalls).toHaveLength(1)
    expect(await requestOf(cycleCalls[0][0] as Request).json()).toEqual({
      crop_id: 1,
      sown_on: '2026-06-01',
    })
  })

  it('shows the 409 detail when the plot already has an active cycle', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'plots/plot-1/cycles': () =>
        jsonResponse(
          {
            type: 'about:blank',
            title: 'Plot already has an active crop cycle',
            status: 409,
            detail: 'Plot already has an active crop cycle',
          },
          409,
        ),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() =>
      expect(screen.getByText('Plot already has an active crop cycle')).toBeInTheDocument(),
    )
  })

  it('ends an active cycle by marking it harvested and returns the start-cycle form', async () => {
    let patchCalls = 0
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      'cycles/cycle-1': () => {
        patchCalls += 1
        return jsonResponse({
          id: 'cycle-1',
          plot_id: 'plot-1',
          crop_id: 1,
          sown_on: '2026-06-01',
          expected_harvest_on: '2026-09-15',
          status: 'harvested',
        })
      },
      '/plots/plot-1/cycles': () =>
        jsonResponse(
          {
            id: 'cycle-1',
            plot_id: 'plot-1',
            crop_id: 1,
            sown_on: '2026-06-01',
            expected_harvest_on: '2026-09-15',
            status: 'active',
          },
          201,
        ),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() => expect(screen.getByText('Estado: Activo')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Registrar cosecha' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Iniciar ciclo' })).toBeInTheDocument(),
    )
    expect(screen.queryByText('Estado: Activo')).not.toBeInTheDocument()
    expect(screen.queryByText('Estado: Cosechado')).not.toBeInTheDocument()
    expect(patchCalls).toBe(1)
  })

  it('shows the latest soil moisture per depth from the readings query', async () => {
    const twelveMinutesAgo = new Date(Date.now() - 12 * 60_000).toISOString()
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/readings': () =>
        jsonResponse({
          series: [
            {
              sensor_id: 1,
              depth_cm: 20,
              points: [
                [new Date(Date.now() - 30 * 60_000).toISOString(), 44.1],
                [twelveMinutesAgo, 42.4567],
              ],
            },
          ],
        }),
    })
    renderSheet()

    expect(await screen.findByText('42.5 %')).toBeInTheDocument()
    expect(screen.getByText('20 cm')).toBeInTheDocument()
    expect(screen.getByText('dato de hace 12 min')).toBeInTheDocument()

    const url = vi
      .mocked(fetch)
      .mock.calls.map(([input]) => requestUrl(input as Request))
      .find((candidate) => candidate.includes('/readings'))
    expect(url).toContain('metric=soil_moisture')
    expect(url).toContain('resolution=raw')
  })

  it('updates the live line when a reading event arrives for this plot', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/readings': () =>
        jsonResponse({ series: [{ sensor_id: 1, depth_cm: 20, points: [['2026-09-25T10:00:00Z', 42.5]] }] }),
      '/stream': () =>
        streamResponse(
          `id: 7\nevent: reading\ndata: {"plot_id":"plot-1","metric":"soil_moisture","value":51.2345,"at":"${new Date().toISOString()}"}\n\n`,
        ),
    })
    renderSheet()

    // The live line is one <p> with three text nodes, so match its whole content.
    expect(await screen.findByText(/Última lectura del nodo: 51\.2 %/)).toBeInTheDocument()
  })

  it('ignores a reading event for another plot', async () => {
    mockFetch({
      '/crops': () => jsonResponse(CROPS),
      '/readings': () =>
        jsonResponse({ series: [{ sensor_id: 1, depth_cm: 20, points: [['2026-09-25T10:00:00Z', 42.5]] }] }),
      '/stream': () =>
        streamResponse(
          `id: 8\nevent: reading\ndata: {"plot_id":"plot-9","metric":"soil_moisture","value":51.2,"at":"${new Date().toISOString()}"}\n\n` +
            `id: 9\nevent: reading\ndata: {"plot_id":"plot-1","metric":"soil_moisture","value":60.0,"at":"${new Date().toISOString()}"}\n\n`,
        ),
    })
    renderSheet()

    // Wait for the matching event to arrive and render, proving the stream processed past the first event.
    expect(await screen.findByText(/Última lectura del nodo: 60(\.0)? %/)).toBeInTheDocument()
    expect(screen.queryByText(/51\.2/)).not.toBeInTheDocument()
  })

  it('says so when the plot has no recent readings', async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    expect(await screen.findByText('Sin lecturas recientes')).toBeInTheDocument()
  })

  it('updates the freshness label as time passes', async () => {
    vi.useFakeTimers()
    const baseTime = Date.parse('2026-09-25T12:00:00Z')
    vi.setSystemTime(baseTime)

    try {
      const oneMinuteAgo = new Date(baseTime - 60_000).toISOString()
      mockFetch({
        '/crops': () => jsonResponse(CROPS),
        '/readings': () =>
          jsonResponse({
            series: [
              {
                sensor_id: 1,
                depth_cm: 20,
                points: [[oneMinuteAgo, 42.5]],
              },
            ],
          }),
      })
      renderSheet()

      await vi.advanceTimersByTimeAsync(0)
      expect(screen.getByText('dato de hace 1 min')).toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(60_000)
      expect(screen.getByText('dato de hace 2 min')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
