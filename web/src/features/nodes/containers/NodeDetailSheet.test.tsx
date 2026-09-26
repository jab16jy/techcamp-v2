import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import type { NodeView } from '../api/nodesApi'
import { NodeDetailSheet } from './NodeDetailSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

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
} satisfies NodeView

const SENSORS = [
  { id: 1, node_id: 'node-1', channel_key: 'soil_moisture_20cm', metric: 'soil_moisture', depth_cm: 20, unit: '%' },
  { id: 2, node_id: 'node-1', channel_key: 'battery', metric: 'battery', depth_cm: null, unit: 'V' },
]

function renderSheet(onOpenChange: (open: boolean) => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const sheet = (open: boolean) => (
    <QueryClientProvider client={queryClient}>
      <NodeDetailSheet open={open} onOpenChange={onOpenChange} node={NODE} />
    </QueryClientProvider>
  )
  const view = render(sheet(true))
  return { onOpenChange, rerender: (open: boolean) => view.rerender(sheet(open)) }
}

function mockFetch(byUrl: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(byUrl).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1]()
  })
}

describe('NodeDetailSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('lists the node sensors with channel, metric, depth and unit', async () => {
    mockFetch({ '/nodes/node-1/sensors': () => jsonResponse(SENSORS) })
    renderSheet()

    expect(await screen.findByText('soil_moisture_20cm')).toBeInTheDocument()
    expect(screen.getByText('soil_moisture')).toBeInTheDocument()
    expect(screen.getByText('20 cm')).toBeInTheDocument()
    expect(screen.getByText('%')).toBeInTheDocument()
    // No GET for calibrations exists (docs/04:83-86), so an uncalibrated sensor says so.
    expect(screen.getAllByText('Sin calibración en esta sesión.')).toHaveLength(2)
  })

  it('shows the calibration created for a sensor on its row', async () => {
    mockFetch({
      '/nodes/node-1/sensors': () => jsonResponse(SENSORS),
      calibrations: () =>
        jsonResponse(
          {
            id: 'cal-1',
            sensor_id: 1,
            version: 1,
            method: 'linear',
            kind: 'lab',
            params: { scale: 0.5, offset: -2 },
            rmse_pct: null,
            valid_from: '2026-09-25T10:00:00Z',
          },
          201,
        ),
    })
    renderSheet()

    await screen.findByText('soil_moisture_20cm')
    fireEvent.click(screen.getAllByRole('button', { name: 'Calibrar' })[0])
    fireEvent.change(await screen.findByLabelText(/Escala/), { target: { value: '0.5' } })
    fireEvent.change(screen.getByLabelText(/Offset/), { target: { value: '-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }))

    await waitFor(() => expect(screen.getByText(/Lineal · Laboratorio/)).toBeInTheDocument())
    expect(screen.getAllByText('Sin calibración en esta sesión.')).toHaveLength(1)
  })

  it('confirms before rotating and shows the new password once', async () => {
    mockFetch({
      '/nodes/node-1/sensors': () => jsonResponse(SENSORS),
      'credentials:rotate': () => jsonResponse({ password: 'rotated-secret' }),
    })
    renderSheet()

    await screen.findByText('soil_moisture_20cm')
    fireEvent.click(screen.getByRole('button', { name: 'Rotar credenciales' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sí, rotar' }))

    expect(await screen.findByText('rotated-secret')).toBeInTheDocument()
    expect(screen.getByText(/no se volverá a mostrar/i)).toBeInTheDocument()
  })

  it('never rotates without the confirmation step', async () => {
    mockFetch({
      '/nodes/node-1/sensors': () => jsonResponse(SENSORS),
      'credentials:rotate': () => jsonResponse({ password: 'rotated-secret' }),
    })
    renderSheet()

    await screen.findByText('soil_moisture_20cm')
    fireEvent.click(screen.getByRole('button', { name: 'Rotar credenciales' }))

    expect(vi.mocked(fetch).mock.calls.some(([input]) =>
      requestOf(input as Request).url.includes('credentials:rotate'),
    )).toBe(false)
  })

  it('discards the rotated password when the sheet is closed and reopened', async () => {
    mockFetch({
      '/nodes/node-1/sensors': () => jsonResponse(SENSORS),
      'credentials:rotate': () => jsonResponse({ password: 'rotated-secret' }),
    })
    const { rerender } = renderSheet()

    await screen.findByText('soil_moisture_20cm')
    fireEvent.click(screen.getByRole('button', { name: 'Rotar credenciales' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sí, rotar' }))
    await screen.findByText('rotated-secret')

    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }))
    rerender(false)
    rerender(true)

    await screen.findByText('soil_moisture_20cm')
    expect(screen.queryByText('rotated-secret')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rotar credenciales' })).toBeInTheDocument()
  })
})
