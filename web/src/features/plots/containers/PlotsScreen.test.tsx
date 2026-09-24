import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { PlotsScreen } from './PlotsScreen'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

describe('PlotsScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('prompts to choose an organization when none is selected', () => {
    render(<PlotsScreen />)

    expect(screen.getByText('Elige una organización')).toBeInTheDocument()
  })

  it('shows the farms and plots for the selected org, with area and irrigation', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/farms/farm-1/plots')) {
        return jsonResponse([
          { id: 'plot-1', farm_id: 'farm-1', name: 'Lote Norte', area_ha: 2.456, irrigation_system: 'drip' },
        ])
      }
      if (url.includes('/farms?org_id=org-1')) {
        return jsonResponse({ items: [{ id: 'farm-1', org_id: 'org-1', name: 'Finca La Esperanza' }], next_cursor: null })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    render(<PlotsScreen />)

    expect(screen.getByText('Cargando parcelas…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Finca La Esperanza')).toBeInTheDocument())
    expect(screen.getByText('Lote Norte')).toBeInTheDocument()
    expect(screen.getByText('2,46 ha · Goteo')).toBeInTheDocument()
  })

  it('shows an empty state when the org has no farms', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ items: [], next_cursor: null }))

    render(<PlotsScreen />)

    await waitFor(() => expect(screen.getByText('Todavía no hay fincas')).toBeInTheDocument())
  })

  it('shows an error state when the request fails', async () => {
    setSession('token-abc', 'org-1')
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ type: 'about:blank', title: 'Organization not found', status: 404 }, 404),
    )

    render(<PlotsScreen />)

    await waitFor(() =>
      expect(screen.getByText('No se pudieron cargar las parcelas')).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument()
  })
})
