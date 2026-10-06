/**
 * "Indicadores de tecnificación" (docs/07:145-148): the plot section for any
 * member, the organization section only for the roles that may read it
 * (D-T0.10), and a month the job never stored shown as absent rather than as a
 * failure (docs/07:147).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { forgetActivePlotId, setActivePlotId } from '../../../lib/api/activePlot'
import { saveMeSeed } from '../../../lib/api/me'
import { clearSession, setSession } from '../../../lib/api/session'
import { IndicatorsScreen } from './IndicatorsScreen'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

const PLOT_INDICATORS = {
  plot_id: 'plot-1',
  month: '2026-09-01',
  monitoring: 0.9,
  record_keeping: 0.5,
  decision: null,
  risk_management: null,
  digital_adoption_index: 72.4,
  computed_at: '2026-10-01T07:00:00Z',
}

const ORG_INDICATORS = {
  org_id: 'org-1',
  month: '2026-09-01',
  mean_digital_adoption_index: 68.5,
  plots_with_index: 3,
  monitored_plots_ratio: 0.75,
  harvested_cycles_ratio: null,
  median_hours_to_first_reading: null,
}

function requestedUrls(): string[] {
  return vi
    .mocked(fetch)
    .mock.calls.map((call) => requestUrl(call[0] as Request))
}

function renderScreen() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <IndicatorsScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('IndicatorsScreen', () => {
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
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/api/v1/plots/plot-1/metrics')) return jsonResponse(PLOT_INDICATORS)
      if (url.includes('/api/v1/organizations/org-1/metrics')) return jsonResponse(ORG_INDICATORS)
      return jsonResponse({}, 200)
    })
  })

  afterEach(() => {
    clearSession()
    forgetActivePlotId('org-1')
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('asks both reads for one month and shows the plot section', async () => {
    renderScreen()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Parcela activa' })).toBeInTheDocument(),
    )
    const plotCall = requestedUrls().find((url) => url.includes('/plots/plot-1/metrics'))
    const orgCall = requestedUrls().find((url) => url.includes('/organizations/org-1/metrics'))
    // D-T9.2: the same chosen month reaches both queries.
    expect(plotCall).toContain('month=2026-')
    expect(orgCall).toContain(plotCall?.split('month=')[1])
    expect(screen.getByText('Adopción digital')).toBeInTheDocument()
  })

  it('shows the organization section to an owner', async () => {
    renderScreen()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Organización' })).toBeInTheDocument(),
    )
    expect(screen.getByText('Parcelas con índice')).toBeInTheDocument()
  })

  it('keeps the organization section and its request from a producer entirely', async () => {
    saveMeSeed('token-abc', {
      id: 'user-2',
      phone: '+573001234567',
      email: null,
      full_name: null,
      memberships: [{ org_id: 'org-1', role: 'producer' }],
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Parcela activa' })).toBeInTheDocument(),
    )
    expect(screen.queryByRole('heading', { name: 'Organización' })).toBeNull()
    // D-T0.10: the read never leaves the device, so it cannot even earn a 403.
    expect(requestedUrls().some((url) => url.includes('/organizations/'))).toBe(false)
  })

  it('reads a month with no stored row as absent rather than as a failure', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/plots/plot-1/metrics')) {
        return jsonResponse({ title: 'Plot has no metrics for that month', status: 404 }, 404)
      }
      if (url.includes('/organizations/')) return jsonResponse(ORG_INDICATORS)
      return jsonResponse({}, 200)
    })

    renderScreen()

    // Scoped to the plot section: the organization's own rows legitimately show
    // the same sentence for the two figures D-T7.1 leaves null.
    const plot = await screen.findByRole('region', { name: 'Parcela activa' })
    await waitFor(() => expect(within(plot).getByText('Sin datos este mes')).toBeInTheDocument())
    expect(within(plot).queryByText('Adopción digital')).toBeNull()
    expect(screen.queryByText('No se pudieron cargar')).toBeNull()
  })

  it('reports a real failure with a way to try again', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/plots/plot-1/metrics')) {
        return jsonResponse({ title: 'Plot not found', status: 404 }, 404)
      }
      return jsonResponse({}, 200)
    })

    renderScreen()

    await waitFor(() =>
      expect(screen.getByText('No se pudieron cargar los indicadores')).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument()
  })

  it('invites the user to open a plot when the device remembers none', async () => {
    forgetActivePlotId('org-1')

    renderScreen()

    await waitFor(() =>
      expect(screen.getByText('No hay una parcela activa')).toBeInTheDocument(),
    )
    expect(screen.getByRole('link', { name: 'Ir a Parcelas' })).toBeInTheDocument()
    // Nothing about a plot is asked for, because there is no plot to ask about.
    expect(requestedUrls().some((url) => url.includes('/plots/'))).toBe(false)
  })
})
