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

function renderSheet(onOpenChange: (open: boolean) => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <PlotDetailSheet open onOpenChange={onOpenChange} plotId="plot-1" plotName="Lote Norte" />
    </QueryClientProvider>,
  )
}

/** Mocks `GET /crops` plus every other call the test provides by URL substring. */
function mockFetch(byUrl: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(byUrl).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1]()
  })
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
      requestOf(input as Request).url.includes('/soil'),
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

  it('disables starting a cycle when the expected harvest is before the sowing date', async () => {
    mockFetch({ '/crops': () => jsonResponse(CROPS) })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.change(screen.getByLabelText(/Cosecha esperada/), { target: { value: '2026-05-01' } })

    expect(
      screen.getByText('La cosecha esperada no puede ser antes de la siembra.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Iniciar ciclo' })).toBeDisabled()
  })

  it('starts a cycle and overrides the expected harvest date, then shows it as active', async () => {
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
      'cycles/cycle-1': () =>
        jsonResponse({
          id: 'cycle-1',
          plot_id: 'plot-1',
          crop_id: 1,
          sown_on: '2026-06-01',
          expected_harvest_on: '2026-10-01',
          status: 'active',
        }),
    })
    renderSheet()

    await waitFor(() => screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Fecha de siembra/), { target: { value: '2026-06-01' } })
    fireEvent.change(screen.getByLabelText(/Cosecha esperada/), { target: { value: '2026-10-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar ciclo' }))

    await waitFor(() =>
      expect(screen.getByText(/cosecha esperada el 2026-10-01/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/Maíz · sembrado el 2026-06-01/)).toBeInTheDocument()

    const [postRequest] = vi.mocked(fetch).mock.calls.find(([input]) =>
      requestOf(input as Request).url.includes('/cycles') &&
      !requestOf(input as Request).url.includes('cycles/cycle-1'),
    )!
    expect(await requestOf(postRequest as Request).json()).toEqual({ crop_id: 1, sown_on: '2026-06-01' })

    const [patchRequest] = vi.mocked(fetch).mock.calls.find(([input]) =>
      requestOf(input as Request).url.includes('cycles/cycle-1'),
    )!
    expect(await requestOf(patchRequest as Request).json()).toEqual({
      expected_harvest_on: '2026-10-01',
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

  it('ends an active cycle by marking it harvested', async () => {
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

    await waitFor(() => expect(screen.getByText('Estado: Cosechado')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Registrar cosecha' })).not.toBeInTheDocument()
    expect(patchCalls).toBe(1)
  })
})
