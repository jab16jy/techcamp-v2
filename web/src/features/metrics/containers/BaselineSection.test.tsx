import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

/** Opens the invitation's form and answers every required question, then saves. */
async function openFormAndSave(cropName: string, yieldKgHa: string, practice: string) {
  fireEvent.click(await screen.findByRole('button', { name: 'Registrar encuesta' }))
  fireEvent.change(await screen.findByLabelText('Fecha de inscripción'), {
    target: { value: '2026-01-15' },
  })
  fireEvent.click(screen.getByLabelText('Cultivo del último ciclo'))
  fireEvent.click(await screen.findByRole('option', { name: cropName }))
  fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
    target: { value: yieldKgHa },
  })
  fireEvent.click(screen.getByLabelText('¿Cómo se regaba antes?'))
  fireEvent.click(await screen.findByRole('option', { name: practice }))
  fireEvent.click(screen.getByRole('button', { name: 'Guardar encuesta' }))
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

    await openFormAndSave('Ñame', '2400', 'Goteo')

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

  it('names the field and the way out when the crop is rejected, never the wire code', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    renderSection('owner')

    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestOf(input as Request).url
      if (url.includes('/crops')) return jsonResponse(CROPS)
      if (requestOf(input as Request).method === 'PUT') {
        return problemResponse(422, 'crop_id is not a valid crop')
      }
      return problemResponse(404, 'Plot has no enrollment survey')
    })

    await openFormAndSave('Maíz', '1500', 'Secano')

    // The server's detail names a Python field; the producer gets the field's
    // own name and the way out instead (docs/07:14).
    expect(
      await screen.findByText('Ese cultivo no está en la lista. Elige otro de la lista de cultivos.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/crop_id/)).not.toBeInTheDocument()
  })

  it('never names the crop for a 422 that names no field', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    renderSection('owner')

    // A `422` the form cannot attribute to a field is not this form's to
    // explain: guessing "the crop" sends the producer after a field the server
    // never refused.
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestOf(input as Request).url
      if (url.includes('/crops')) return jsonResponse(CROPS)
      if (requestOf(input as Request).method === 'PUT') {
        return problemResponse(422, 'Input should be a valid number')
      }
      return problemResponse(404, 'Plot has no enrollment survey')
    })

    await openFormAndSave('Maíz', '1500', 'Secano')

    expect(
      await screen.findByText('No se pudo guardar la encuesta. Revisa los datos e intenta de nuevo.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Ese cultivo no está en la lista/)).not.toBeInTheDocument()
    expect(screen.queryByText(/crop_id/)).not.toBeInTheDocument()
  })

  it('announces a read failure a screen reader can hear', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(500, 'Server error'),
    })
    renderSection('owner')

    // Nothing else on the page changes when a read fails, so without an
    // announcement the failure is silent: the same repair the save error got.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Ocurrió un error. Intenta de nuevo.')
  })

  it('does not offer a form it cannot fill when the crop catalog fails', async () => {
    routeFetch({
      '/crops': () => problemResponse(500, 'Catalog unavailable'),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    const queryClient = renderSection('owner')

    expect(await screen.findByText(/Sin encuesta todavía se puede sembrar/)).toBeInTheDocument()
    // A crop Select with no items plus a required crop leaves the submit
    // disabled forever, so the block is announced and the invitation waits
    // instead of opening a dead form. No retry is added: `cropsQuery` is the
    // sheet's shared catalog and that sheet already offers one recovery.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'La lista de cultivos no cargó, así que la encuesta no se puede llenar todavía.',
    )
    expect(screen.queryByRole('button', { name: 'Registrar encuesta' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reintentar' })).not.toBeInTheDocument()

    // Whenever the shared catalog recovers — by the sheet's retry or any other
    // refetch of the same query — the invitation comes back.
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => problemResponse(404, 'Plot has no enrollment survey'),
    })
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['crops'] })
    })

    expect(await screen.findByRole('button', { name: 'Registrar encuesta' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('prints the enrollment date the way a Colombian reads it, not a day early', async () => {
    routeFetch({
      '/crops': () => jsonResponse(CROPS),
      '/baseline': () => jsonResponse(SURVEY),
    })
    renderSection('owner')

    // `new Date('2026-01-15')` is UTC midnight, which in Bogotá is still the
    // 14th: the summary must not slip a day. `es-CO` prints the day
    // unpadded, so the expectation follows the locale rather than a guess.
    expect(await screen.findByText(/^15\/1\/2026$/)).toBeInTheDocument()
    expect(screen.queryByText(/^14\/1\/2026$/)).not.toBeInTheDocument()
  })
})