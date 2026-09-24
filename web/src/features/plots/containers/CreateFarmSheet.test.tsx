import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { CreateFarmSheet } from './CreateFarmSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function renderSheet(onOpenChange: (open: boolean) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <CreateFarmSheet open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  )
}

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Finca La Esperanza' } })
  fireEvent.change(screen.getByLabelText('Código de municipio (DIVIPOLA)'), { target: { value: '20001' } })
  fireEvent.change(screen.getByLabelText('Latitud'), { target: { value: '10.46' } })
  fireEvent.change(screen.getByLabelText('Longitud'), { target: { value: '-73.25' } })
}

describe('CreateFarmSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('submits a farm with the org id, name, municipality and a GeoJSON point location', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: 'farm-1',
        org_id: 'org-1',
        name: 'Finca La Esperanza',
        municipality_code: '20001',
        location: { type: 'Point', coordinates: [-73.25, 10.46] },
        technician_id: null,
      }),
    )
    const onOpenChange = vi.fn()
    renderSheet(onOpenChange)

    fillRequiredFields()
    fireEvent.click(screen.getByRole('button', { name: 'Crear finca' }))

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    const [request] = vi.mocked(fetch).mock.calls[0]
    const body = await (request as Request).json()
    expect(body).toEqual({
      org_id: 'org-1',
      name: 'Finca La Esperanza',
      municipality_code: '20001',
      location: { type: 'Point', coordinates: [-73.25, 10.46] },
    })
  })

  it('disables the submit button until the required fields are filled', () => {
    renderSheet(vi.fn())

    expect(screen.getByRole('button', { name: 'Crear finca' })).toBeDisabled()

    fillRequiredFields()

    expect(screen.getByRole('button', { name: 'Crear finca' })).not.toBeDisabled()
  })

  it('shows the 422 detail on the form and keeps the sheet open', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        { type: 'about:blank', title: 'Validation error', status: 422, detail: 'name: Field required' },
        422,
      ),
    )
    const onOpenChange = vi.fn()
    renderSheet(onOpenChange)

    fillRequiredFields()
    fireEvent.click(screen.getByRole('button', { name: 'Crear finca' }))

    await waitFor(() => expect(screen.getByText('name: Field required')).toBeInTheDocument())
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })
})
