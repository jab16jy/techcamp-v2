import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { BaselineSection } from './BaselineSection'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function problemResponse(status: number, title: string, detail?: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title, ...(detail ? { detail } : {}), status }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

const CROPS = [
  { id: 1, code: 'maize', name_es: 'Maíz', kc_source: 'fao56', stages: [] },
  { id: 2, code: 'yam', name_es: 'Ñame', kc_source: 'none', stages: [] },
]

const SURVEY = {
  plot_id: 'plot-1',
  org_id: 'org-1',
  enrolled_on: '2026-01-15',
  crop_id: 2,
  last_yield_kg_ha: 2400,
  last_cost_cop_ha: null,
  irrigation_practice: 'drip',
  recorded_by: 'user-1',
}

function renderSection(role: 'owner' | 'technician' | 'producer' | null = 'owner') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <BaselineSection plotId="plot-1" callerRole={role} />
    </QueryClientProvider>,
  )
  return queryClient
}

type Route = (request: Request) => Response

function routeFetch(handlers: Record<string, Route>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(handlers).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1](request)
  })
}

describe('BaselineSection', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('shows the recorded survey with an unknown cost as unknown, not as zero', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => jsonResponse(SURVEY),
    })
    renderSection('owner')

    await waitFor(() => expect(screen.getByText('Ñame')).toBeInTheDocument())
    expect(screen.getByText(/2\.400/)).toBeInTheDocument()
    expect(screen.getByText('Goteo')).toBeInTheDocument()
    // A missing figure is missing evidence (docs/03:426-430), never a free plot.
    expect(screen.getByText('No lo registró')).toBeInTheDocument()
  })

  it('invites a role that may fill it to register the first survey', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    renderSection('technician')

    expect(
      await screen.findByText(/Sin encuesta todavía se puede sembrar, pero no se puede medir el cambio/),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Registrar encuesta' }))

    expect(await screen.findByRole('button', { name: 'Guardar encuesta' })).toBeInTheDocument()
  })

  it('shows a read-only role the survey without offering the form', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => jsonResponse(SURVEY),
    })
    renderSection('producer')

    await waitFor(() => expect(screen.getByText('Ñame')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Editar encuesta' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Registrar encuesta' })).not.toBeInTheDocument()
  })

  it('tells a read-only role the survey is missing instead of offering a save it would refuse', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    renderSection('producer')

    expect(
      await screen.findByText(/Solo el dueño o un técnico pueden registrar la encuesta/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Registrar encuesta' })).not.toBeInTheDocument()
  })

  it('explains the 403 the server answers when a role that may not fill it still tries', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => jsonResponse(SURVEY),
    })
    renderSection('producer')

    await waitFor(() => expect(screen.getByText('Ñame')).toBeInTheDocument())
    expect(screen.getByText('Tu rol no puede cambiar esta encuesta.')).toBeInTheDocument()
  })

  it('shows the saved survey after a successful save, without a second read', async () => {
    let reads = 0
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': (request) => {
        // Only the GET counts: the PUT answers with the same path, and its
        // response is exactly what fills the cache this test is about.
        if (request.method === 'PUT') return jsonResponse(SURVEY)
        reads += 1
        return reads === 1
          ? problemResponse(404, 'Plot has no enrollment survey')
          : jsonResponse(SURVEY)
      },
    })
    renderSection('owner')

    fireEvent.click(await screen.findByRole('button', { name: 'Registrar encuesta' }))
    fireEvent.change(await screen.findByLabelText('Fecha de inscripción'), {
      target: { value: '2026-01-15' },
    })
    fireEvent.click(screen.getByLabelText('Cultivo del último ciclo'))
    fireEvent.click(await screen.findByRole('option', { name: 'Ñame' }))
    fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
      target: { value: '2400' },
    })
    fireEvent.click(screen.getByLabelText('¿Cómo se regaba antes?'))
    fireEvent.click(await screen.findByRole('option', { name: 'Goteo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar encuesta' }))

    // The form closes on a successful save. Waiting for it matters: while the
    // sheet is still open its own "Goteo" (the chosen option) is in the
    // document, and an assertion on that text would pass without the summary
    // ever having rendered.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Guardar encuesta' })).not.toBeInTheDocument(),
    )
    expect(screen.getByText('Goteo')).toBeInTheDocument()
    expect(screen.getByText('Ñame')).toBeInTheDocument()
    // The PUT response is the stored row and `usePutBaseline` caches it under
    // the read key, so exactly one GET ever ran.
    expect(reads).toBe(1)
  })

  it('shows the server detail when the crop is rejected as outside the catalog', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    renderSection('owner')

    fireEvent.click(await screen.findByRole('button', { name: 'Registrar encuesta' }))
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestOf(input as Request).url
      if (url.includes('/crops')) return jsonResponse(CROPS)
      if (requestOf(input as Request).method === 'PUT') {
        return problemResponse(422, 'crop_id is not a valid crop')
      }
      return problemResponse(404, 'Plot has no enrollment survey')
    })

    fireEvent.change(await screen.findByLabelText('Fecha de inscripción'), {
      target: { value: '2026-01-15' },
    })
    fireEvent.click(screen.getByLabelText('Cultivo del último ciclo'))
    fireEvent.click(await screen.findByRole('option', { name: 'Maíz' }))
    fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
      target: { value: '1500' },
    })
    fireEvent.click(screen.getByLabelText('¿Cómo se regaba antes?'))
    fireEvent.click(await screen.findByRole('option', { name: 'Secano' }))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar encuesta' }))

    expect(await screen.findByText('crop_id is not a valid crop')).toBeInTheDocument()
  })
})