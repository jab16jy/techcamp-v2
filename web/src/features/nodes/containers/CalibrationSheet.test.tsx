import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { CalibrationSheet } from './CalibrationSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

const SENSOR = { id: 1, channel_key: 'soil_moisture_20cm' }

const CREATED = {
  id: 'cal-1',
  sensor_id: 1,
  version: 1,
  method: 'linear',
  kind: 'lab',
  params: { scale: 0.5, offset: -2 },
  rmse_pct: 1.5,
  valid_from: '2026-09-25T10:00:00Z',
}

function renderSheet(onCreated = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <CalibrationSheet open onOpenChange={vi.fn()} nodeId="node-1" sensor={SENSOR} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return onCreated
}

function mockFetch(byUrl: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(byUrl).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1]()
  })
}

async function chooseMethod(label: string) {
  fireEvent.click(screen.getByLabelText('Método'))
  fireEvent.click(await screen.findByRole('option', { name: label }))
}

describe('CalibrationSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('sends a linear calibration with the docs/03 params', async () => {
    mockFetch({ calibrations: () => jsonResponse(CREATED, 201) })
    const onCreated = renderSheet()

    fireEvent.change(screen.getByLabelText(/Escala/), { target: { value: '0.5' } })
    fireEvent.change(screen.getByLabelText(/Offset/), { target: { value: '-2' } })
    fireEvent.change(screen.getByLabelText(/RMSE/), { target: { value: '1.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED))
    const [input] = vi.mocked(fetch).mock.calls[0]
    const body = await requestOf(input as Request).json()
    expect(requestOf(input as Request).url).toContain('/sensors/1/calibrations')
    expect(body.method).toBe('linear')
    expect(body.kind).toBe('lab')
    expect(body.params).toEqual({ scale: 0.5, offset: -2 })
    expect(body.rmse_pct).toBe(1.5)
    // Default: the calibration is valid from now, not from a stale date.
    expect(Math.abs(Date.parse(body.valid_from) - Date.now())).toBeLessThan(60_000)
  })

  it('keeps rmse_pct null when it is left empty', async () => {
    mockFetch({ calibrations: () => jsonResponse(CREATED, 201) })
    const onCreated = renderSheet()

    fireEvent.change(screen.getByLabelText(/Escala/), { target: { value: '0.5' } })
    fireEvent.change(screen.getByLabelText(/Offset/), { target: { value: '-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }))

    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    const [input] = vi.mocked(fetch).mock.calls[0]
    expect((await requestOf(input as Request).json()).rmse_pct).toBeNull()
  })

  it('shows the server detail when the params are rejected', async () => {
    mockFetch({
      calibrations: () =>
        jsonResponse(
          {
            type: 'about:blank',
            title: 'Invalid calibration params',
            detail: "linear requires 'scale'",
            status: 422,
          },
          422,
        ),
    })
    renderSheet()

    fireEvent.change(screen.getByLabelText(/Escala/), { target: { value: '0.5' } })
    fireEvent.change(screen.getByLabelText(/Offset/), { target: { value: '-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }))

    expect(await screen.findByText("linear requires 'scale'")).toBeInTheDocument()
  })

  it('asks for the two_point params the server validates', async () => {
    mockFetch({ calibrations: () => jsonResponse(CREATED, 201) })
    renderSheet()

    await chooseMethod('Dos puntos')

    expect(screen.getByLabelText(/raw_dry/)).toBeInTheDocument()
    expect(screen.getByLabelText(/raw_wet/)).toBeInTheDocument()
    expect(screen.getByLabelText(/vwc_dry/)).toBeInTheDocument()
    expect(screen.getByLabelText(/vwc_wet/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Escala/)).not.toBeInTheDocument()
  })

  it('sends the polynomial coeffs as a list of numbers', async () => {
    mockFetch({ calibrations: () => jsonResponse({ ...CREATED, method: 'polynomial' }, 201) })
    const onCreated = renderSheet()

    await chooseMethod('Polinomio')
    fireEvent.change(screen.getByLabelText(/Coeficientes/), { target: { value: '0.1, 0.02, 0.0003' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar calibración' }))

    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    const [input] = vi.mocked(fetch).mock.calls[0]
    const body = await requestOf(input as Request).json()
    expect(body.method).toBe('polynomial')
    expect(body.params).toEqual({ coeffs: [0.1, 0.02, 0.0003] })
  })

  it('keeps the submit action disabled until the method params are filled', async () => {
    mockFetch({ calibrations: () => jsonResponse(CREATED, 201) })
    renderSheet()

    expect(screen.getByRole('button', { name: 'Guardar calibración' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Escala/), { target: { value: '0.5' } })
    expect(screen.getByRole('button', { name: 'Guardar calibración' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Offset/), { target: { value: '-2' } })
    expect(screen.getByRole('button', { name: 'Guardar calibración' })).toBeEnabled()
  })
})
