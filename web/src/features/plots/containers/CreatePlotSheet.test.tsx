import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import type { PlotDrawMapProps } from '../components/PlotDrawMap'
import { CreatePlotSheet } from './CreatePlotSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

// The Leaflet map itself is mocked (task instruction): this stand-in exposes
// one button per click that adds a vertex a bit further northeast each time,
// so tests can build a valid polygon without a real map.
vi.mock('../components/PlotDrawMap', () => ({
  default: ({ vertices, onMapClick }: PlotDrawMapProps) => (
    <div>
      <button
        type="button"
        onClick={() => onMapClick({ lat: 10 + vertices.length, lng: -74 + vertices.length })}
      >
        add-vertex
      </button>
      <span>{vertices.length} vertices</span>
    </div>
  ),
}))

function renderSheet(onOpenChange: (open: boolean) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <CreatePlotSheet open onOpenChange={onOpenChange} farmId="farm-1" />
    </QueryClientProvider>,
  )
}

/** Clicks the mocked map's add-vertex button `times` more times, settling after each click. */
async function clickAddVertex(times: number) {
  const button = await screen.findByRole('button', { name: 'add-vertex' })
  const before = Number(screen.getByText(/\d+ vertices/).textContent?.match(/\d+/)?.[0] ?? 0)
  for (let i = 1; i <= times; i += 1) {
    fireEvent.click(button)
    const expected = before + i
    await waitFor(() => expect(screen.getByText(`${expected} vertices`)).toBeInTheDocument())
  }
}

describe('CreatePlotSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('disables submit until the plot has a name and at least 3 vertices', async () => {
    renderSheet(vi.fn())

    const submit = screen.getByRole('button', { name: 'Crear parcela' })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Lote Norte' } })
    await clickAddVertex(2)
    expect(submit).toBeDisabled()

    await clickAddVertex(1)
    expect(submit).not.toBeDisabled()
  })

  it('hides irrigation efficiency and flow for a rainfed (secano) plot by default', () => {
    renderSheet(vi.fn())

    expect(screen.queryByLabelText(/Eficiencia/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Caudal/)).not.toBeInTheDocument()
  })

  it('shows irrigation efficiency and flow once an irrigated system is selected', () => {
    renderSheet(vi.fn())

    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('option', { name: 'Goteo' }))

    expect(screen.getByLabelText(/Eficiencia/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Caudal/)).toBeInTheDocument()
  })

  it('submits the closed [lon, lat] polygon and irrigation fields, closing the sheet on success', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: 'plot-1',
        org_id: 'org-1',
        farm_id: 'farm-1',
        name: 'Lote Norte',
        boundary: { type: 'Polygon', coordinates: [[]] },
        area_ha: 1,
        weather_cell_id: null,
        irrigation_system: 'drip',
        irrigation_efficiency: 0.9,
        system_flow_lph: 200,
      }),
    )
    const onOpenChange = vi.fn()
    renderSheet(onOpenChange)

    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Lote Norte' } })
    await clickAddVertex(3)
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('option', { name: 'Goteo' }))
    fireEvent.change(screen.getByLabelText(/Eficiencia/), { target: { value: '0.9' } })
    fireEvent.change(screen.getByLabelText(/Caudal/), { target: { value: '200' } })

    fireEvent.click(screen.getByRole('button', { name: 'Crear parcela' }))

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    const [request] = vi.mocked(fetch).mock.calls[0]
    const body = await (request as Request).json()
    expect(body).toEqual({
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
      },
      irrigation_system: 'drip',
      irrigation_efficiency: 0.9,
      system_flow_lph: 200,
    })
  })

  it('shows the 422 detail on the form and keeps the sheet open', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        { type: 'about:blank', title: 'Validation error', status: 422, detail: 'boundary: invalid ring' },
        422,
      ),
    )
    const onOpenChange = vi.fn()
    renderSheet(onOpenChange)

    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Lote Norte' } })
    await clickAddVertex(3)
    fireEvent.click(screen.getByRole('button', { name: 'Crear parcela' }))

    await waitFor(() => expect(screen.getByText('boundary: invalid ring')).toBeInTheDocument())
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })
})
