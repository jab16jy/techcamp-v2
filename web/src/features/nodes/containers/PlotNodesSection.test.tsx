import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { PlotNodesSection } from './PlotNodesSection'

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
}

const HEALTH = {
  last_seen_at: '2026-09-25T10:00:00Z',
  battery_v: 3.9,
  rssi: -78,
  completeness_24h: 98.4,
}

function nodePage(nodes: unknown[]) {
  return { items: nodes, next_cursor: null }
}

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <PlotNodesSection plotId="plot-1" />
    </QueryClientProvider>,
  )
}

function mockFetch(byUrl: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(byUrl).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1]()
  })
}

describe('PlotNodesSection', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('lists the plot nodes with their status and health', async () => {
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([NODE])),
      '/health': () => jsonResponse(HEALTH),
    })
    renderSection()

    expect(await screen.findByText('70B3D5A3B0000001')).toBeInTheDocument()
    expect(screen.getByText('En línea')).toBeInTheDocument()
    // The health request is per node, so the row's numbers land after the list does.
    expect(await screen.findByText('3.9 V')).toBeInTheDocument()
    expect(screen.getByText('-78 dBm')).toBeInTheDocument()
    expect(screen.getByText('98.4 %')).toBeInTheDocument()
  })

  it('reads the nodes list scoped to the plot and the org', async () => {
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([NODE])),
      '/health': () => jsonResponse(HEALTH),
    })
    renderSection()

    await screen.findByText('70B3D5A3B0000001')
    const url = requestOf(vi.mocked(fetch).mock.calls[0][0] as Request).url
    expect(url).toContain('org_id=org-1')
    expect(url).toContain('plot_id=plot-1')
  })

  it('shows how long ago each node was last seen', async () => {
    const twelveMinutesAgo = new Date(Date.now() - 12 * 60_000).toISOString()
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([NODE])),
      '/health': () => jsonResponse({ ...HEALTH, last_seen_at: twelveMinutesAgo }),
    })
    renderSection()

    expect(await screen.findByText('dato de hace 12 min')).toBeInTheDocument()
  })

  it('shows a dash, with no unit, for every health field the node has not reported', async () => {
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([NODE])),
      '/health': () =>
        jsonResponse({ last_seen_at: null, battery_v: null, rssi: null, completeness_24h: null }),
    })
    renderSection()

    await screen.findByText('70B3D5A3B0000001')
    expect(screen.getAllByText('—')).toHaveLength(4)
    expect(screen.queryByText('— V')).not.toBeInTheDocument()
    expect(screen.queryByText('— dBm')).not.toBeInTheDocument()
    expect(screen.queryByText('— %')).not.toBeInTheDocument()
  })

  it('falls back to the node id when the node has no dev eui', async () => {
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([{ ...NODE, dev_eui: null }])),
      '/health': () => jsonResponse(HEALTH),
    })
    renderSection()

    expect(await screen.findByText('node-1')).toBeInTheDocument()
  })

  it('shows the empty state when the plot has no nodes', async () => {
    mockFetch({ '/nodes?': () => jsonResponse(nodePage([])) })
    renderSection()

    expect(await screen.findByText('Sin nodos')).toBeInTheDocument()
  })

  it('shows the loading state while the nodes list is in flight', () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}))
    renderSection()

    expect(screen.getByText('Cargando nodos…')).toBeInTheDocument()
  })

  it('shows error copy and a working retry when the nodes list fails', async () => {
    let attempts = 0
    mockFetch({
      '/nodes?': () => {
        attempts += 1
        return attempts === 1
          ? jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500)
          : jsonResponse(nodePage([NODE]))
      },
      '/health': () => jsonResponse(HEALTH),
    })
    renderSection()

    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))

    expect(await screen.findByText('70B3D5A3B0000001')).toBeInTheDocument()
    expect(attempts).toBe(2)
  })

  it('shows error copy in the row when a single node health request fails', async () => {
    mockFetch({
      '/nodes?': () => jsonResponse(nodePage([NODE])),
      '/health': () => jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500),
    })
    renderSection()

    expect(await screen.findByText('70B3D5A3B0000001')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument(),
    )
  })
})
