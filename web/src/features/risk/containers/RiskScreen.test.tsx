/**
 * Riesgo climático (docs/07 §Mapa de pantallas): the screen answers with the
 * predictions of the active plot, and says plainly when there are none.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { forgetActivePlotId, setActivePlotId } from '../../../lib/api/activePlot'
import { saveMeSeed } from '../../../lib/api/me'
import { clearSession, setSession } from '../../../lib/api/session'
import { RiskScreen } from './RiskScreen'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const PREDICTION = {
  event_type: 'flood',
  horizon_start: '2026-11-01',
  horizon_days: 30,
  probability: 0.08547,
  severity: 'low',
  top_factors: [{ feature: 'precip_sum_6m', value: 241, contribution: 0.31 }],
  model_version: {
    name: 'climatology_month',
    version: '1',
    is_baseline: true,
    metrics: null,
  },
  created_at: '2026-10-01T06:00:00Z',
}

function renderScreen() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RiskScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('RiskScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
    setActivePlotId('org-1', 'plot-1')
    saveMeSeed('token-abc', {
      id: 'user-1',
      phone: '+573001234567',
      email: null,
      full_name: null,
      memberships: [{ org_id: 'org-1', role: 'owner' }],
    })
  })

  afterEach(() => {
    clearSession()
    forgetActivePlotId('org-1')
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('shows the prediction of the active plot', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/plots/plot-1/risk')) return jsonResponse([PREDICTION])
      return jsonResponse({}, 200)
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Inundación' })).toBeInTheDocument(),
    )
    expect(screen.getByText('8.5 %')).toBeInTheDocument()
    // D-DH.12: the origin is named and no severity is declared for a version
    // without calibrated cuts.
    expect(screen.queryByText('Severidad')).toBeNull()
    expect(
      screen.getByText('Origen: climatology_month v1 · línea base · sin cortes calibrados'),
    ).toBeInTheDocument()
  })

  it('says plainly that the plot has no prediction yet', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/plots/plot-1/risk')) return jsonResponse([])
      return jsonResponse({}, 200)
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByText('Sin predicciones de riesgo')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('heading', { name: 'Inundación' })).toBeNull()
  })

  it('says the risk could not be loaded when the read fails', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/plots/plot-1/risk')) {
        return jsonResponse({ title: 'Plot not found', status: 404 }, 404)
      }
      return jsonResponse({}, 200)
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudo cargar el riesgo climático')).toBeInTheDocument(),
    )
  })
})
