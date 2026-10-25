import 'fake-indexeddb/auto'
import { fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as triggers from '../lib/sync/triggers'
import * as db from '../lib/db/db'
import { clearSession, setSession } from '../lib/api/session'
import { getSyncState, recordSyncOutcome } from '../lib/sync/syncState'
import { buildRoutes } from './routes'

import { screen } from '@testing-library/react'
import { saveMeSeed } from '../lib/api/me'

describe('buildRoutes', () => {
  it('excludes the dev-only catalog route when none is supplied', () => {
    const routes = buildRoutes(null)

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(false)
  })

  it('nests the supplied dev-only catalog route under the app shell', () => {
    const routes = buildRoutes({ path: 'dev/ui', element: null })

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(true)
  })

  it('includes the five tabs, the estado route and the indicators screen at the root', () => {
    const routes = buildRoutes(null)

    expect(routes[0].path).toBe('/')
    expect(routes[0].children?.some((route) => route.index === true)).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'estado')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'alertas')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'bitacora')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'parcelas')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'mas')).toBe(true)
    // docs/07's screen map: `Más --> Indicadores de tecnificación`.
    expect(routes[0].children?.some((route) => route.path === 'mas/indicadores')).toBe(true)
    // docs/07:129: `more --> assist[Asistente]`.
    expect(routes[0].children?.some((route) => route.path === 'mas/asistente')).toBe(true)
    expect(routes[0].children?.length).toBe(8)
  })

  /**
   * A screen that cannot be reached is not a delivered screen, and docs/07:129
   * hangs the assistant off `Más`. The link and the route are asserted together
   * because either one alone leaves the producer at a dead end.
   */
  it('reaches the assistant screen from the Más list', async () => {
    setSession('token-prod', 'org-1')
    saveMeSeed('token-prod', {
      id: 'prod-1',
      phone: '+573001234568',
      email: null,
      full_name: 'Productor',
      memberships: [{ org_id: 'org-1', role: 'producer' }],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/mas'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    const link = await screen.findByRole('link', { name: 'Asistente' })
    expect(link).toHaveAttribute('href', '/mas/asistente')

    fireEvent.click(link)

    // No plot remembered: the screen says so instead of offering a question it
    // could not answer (AssistantScreen's empty state).
    await waitFor(() => expect(screen.getByText('No hay una parcela activa')).toBeInTheDocument())
  })
})

describe('role-based routing and /estado', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (input) => {
        const url =
          typeof input === 'string'
            ? input
            : input instanceof Request
              ? input.url
              : String(input)
        if (url.includes('/api/v1/me/tray')) {
          return new Response(JSON.stringify([]), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/api/v1/farms')) {
          return new Response(JSON.stringify({ items: [], next_cursor: null }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
  })

  afterEach(() => {
    clearSession()
    localStorage.clear()
    onlineManager.setOnline(true)
    vi.unstubAllGlobals()
  })

  it('renders technician tray on Inicio for technician role', async () => {
    setSession('token-tech', 'org-1')
    saveMeSeed('token-tech', {
      id: 'tech-1',
      phone: '+573001234567',
      email: null,
      full_name: 'Técnico',
      memberships: [{ org_id: 'org-1', role: 'technician' }],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Bandeja del técnico' })).toBeInTheDocument(),
    )
    // Negative assertion: PlotStatusScreen is not shown for technician on Inicio
    expect(screen.queryByText('Sin parcelas')).toBeNull()
  })

  it('renders PlotStatusScreen on Inicio for producer or owner role', async () => {
    setSession('token-prod', 'org-1')
    saveMeSeed('token-prod', {
      id: 'prod-1',
      phone: '+573001234568',
      email: null,
      full_name: 'Productor',
      memberships: [{ org_id: 'org-1', role: 'producer' }],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(screen.getByText('Sin parcelas')).toBeInTheDocument())
    // Negative assertion: TrayScreen is not shown for producer
    expect(screen.queryByRole('heading', { name: 'Bandeja del técnico' })).toBeNull()
  })

  it('renders /estado with PlotStatusScreen and a way back to Inicio ("Volver a la bandeja")', async () => {
    setSession('token-tech', 'org-1')
    saveMeSeed('token-tech', {
      id: 'tech-1',
      phone: '+573001234567',
      email: null,
      full_name: 'Técnico',
      memberships: [{ org_id: 'org-1', role: 'technician' }],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/estado'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    const backLink = await screen.findByRole('link', { name: /Volver a la bandeja/i })
    expect(backLink).toBeInTheDocument()
    // Negative assertion: no unicode glyph '←' used as an icon
    expect(backLink.textContent).not.toContain('←')

    await waitFor(() => expect(screen.getByText('Sin parcelas')).toBeInTheDocument())
    // Negative assertion: TrayScreen is not on /estado
    expect(screen.queryByRole('heading', { name: 'Bandeja del técnico' })).toBeNull()
  })

  it('renders neither screen and makes no /status or /farms request while /me is pending; resolving as technician shows the tray', async () => {
    setSession('token-tech', 'org-1')
    // No saveMeSeed: first sign-in before /me data arrives

    let resolveMe!: (value: Response) => void
    const mePromise = new Promise<Response>((resolve) => {
      resolveMe = resolve
    })

    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
      if (url.includes('/api/v1/me/tray')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.includes('/api/v1/me')) {
        return mePromise
      }
      if (url.includes('/api/v1/farms')) {
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    // Allow lazy imports to settle while /me is still pending
    await new Promise((r) => setTimeout(r, 50))

    // While /me is pending: neither screen is rendered
    expect(screen.queryByRole('heading', { name: 'Bandeja del técnico' })).toBeNull()
    expect(screen.queryByText('Sin parcelas')).toBeNull()
    expect(screen.queryByText('Cargando parcelas…')).toBeNull()

    // Negative assertions: makes NO /status or /farms requests while /me is pending
    const requestedUrls = fetchMock.mock.calls.map((call) =>
      typeof call[0] === 'string'
        ? call[0]
        : call[0] instanceof Request
          ? call[0].url
          : String(call[0]),
    )
    expect(requestedUrls.some((u) => u.includes('/status'))).toBe(false)
    expect(requestedUrls.some((u) => u.includes('/farms'))).toBe(false)

    // Resolve /me as technician
    resolveMe(
      new Response(
        JSON.stringify({
          id: 'tech-1',
          phone: '+573001234567',
          email: null,
          full_name: 'Técnico',
          memberships: [{ org_id: 'org-1', role: 'technician' }],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    )

    // Resolving as technician shows the tray
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Bandeja del técnico' })).toBeInTheDocument(),
    )
    expect(screen.queryByText('Sin parcelas')).toBeNull()
  })

  /**
   * A PAUSED fetch, not a slow one: in v5 `isLoading === isPending && isFetching`,
   * so an uncached `/me` whose request cannot leave the device is pending but not
   * loading. Choosing a screen on `isLoading` alone picked the plot status screen
   * for a technician the moment the phone lost signal (#226).
   */
  it('waits for a paused /me before choosing Inicio: no screen flashes and no /status or /farms request is fired', async () => {
    setSession('token-tech', 'org-1')
    // No saveMeSeed: nothing about this user is on the device yet.

    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
      if (url.includes('/api/v1/me/tray')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.includes('/api/v1/farms')) {
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.includes('/api/v1/me')) {
        return new Response(
          JSON.stringify({
            id: 'tech-1',
            phone: '+573001234567',
            email: null,
            full_name: 'Técnico',
            memberships: [{ org_id: 'org-1', role: 'technician' }],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    // Warm both lazy route chunks BEFORE the first render, so the assertions
    // below are about the CHOSEN screen, not about an unresolved dynamic import.
    await Promise.all([
      import('../features/plot-status/containers/PlotStatusScreen'),
      import('../features/visits/containers/TrayScreen'),
    ])

    // Offline BEFORE the queries exist, so the /me request is paused from its
    // first attempt: pending, and not fetching.
    onlineManager.setOnline(false)

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() =>
      expect(queryClient.getQueryState(['me', 'token-tech'])?.fetchStatus).toBe('paused'),
    )
    await new Promise((r) => setTimeout(r, 50))

    // Neither screen renders while the role is unknown
    expect(screen.queryByRole('heading', { name: 'Bandeja del técnico' })).toBeNull()
    expect(screen.queryByText('Cargando parcelas…')).toBeNull()
    expect(screen.queryByText('Sin parcelas')).toBeNull()

    // Back online: /me answers `technician`, so the tray opens.
    onlineManager.setOnline(true)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Bandeja del técnico' })).toBeInTheDocument(),
    )

    // Negative assertion: a technician's Inicio never asks for plot status or farms.
    const requestedUrls = fetchMock.mock.calls.map((call) =>
      typeof call[0] === 'string'
        ? call[0]
        : call[0] instanceof Request
          ? call[0].url
          : String(call[0]),
    )
    expect(requestedUrls.some((u) => u.includes('/api/v1/me/tray'))).toBe(true)
    expect(requestedUrls.some((u) => u.includes('/status'))).toBe(false)
    expect(requestedUrls.some((u) => u.includes('/farms'))).toBe(false)
  })

  /**
   * The other side of the same condition: a `/me` that ERRORED is not pending, so
   * Inicio falls through to the plot status screen instead of rendering nothing
   * forever. Waiting on `isPending` must not swallow the error fallback.
   */
  it('falls back to the plot status screen when /me errors (not pending, not loading)', async () => {
    setSession('token-tech', 'org-1')
    // No saveMeSeed: nothing about this user is on the device yet.

    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
      if (url.includes('/api/v1/me/tray')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.includes('/api/v1/farms')) {
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.includes('/api/v1/me')) {
        return new Response(JSON.stringify({ title: 'Server Error', status: 500 }), {
          status: 500,
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    await import('../features/plot-status/containers/PlotStatusScreen')

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(queryClient.getQueryState(['me', 'token-tech'])?.status).toBe('error'))
    await waitFor(() => expect(screen.getByText('Sin parcelas')).toBeInTheDocument())
    // Negative assertion: an unreadable role never shows the technician tray.
    expect(screen.queryByRole('heading', { name: 'Bandeja del técnico' })).toBeNull()
  })
})

describe('shell wiring (ADR-0005, synchronizer lifecycle)', () => {
  let stopSyncSpy: () => void

  beforeEach(() => {
    stopSyncSpy = vi.fn()
    vi.spyOn(triggers, 'startSynchronizer').mockImplementation(() => stopSyncSpy)
    vi.spyOn(db, 'requestPersistentStorage').mockImplementation(() => {})
  })

  afterEach(() => {
    clearSession()
    vi.restoreAllMocks()
  })

  it('starts the synchronizer once per signed-in session and calls requestPersistentStorage', async () => {
    setSession('token-abc', 'org-1')
    const queryClient = new QueryClient()
    const memoryRouter = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(db.requestPersistentStorage).toHaveBeenCalledTimes(1)
      expect(triggers.startSynchronizer).toHaveBeenCalledTimes(1)
    })

    // Stopping on sign-out / unmount
    unmount()
    expect(stopSyncSpy).toHaveBeenCalledTimes(1)
  })

  it('resets syncState on sign-out / unmount', async () => {
    setSession('token-abc', 'org-1')
    recordSyncOutcome({ status: 'synced', pushed: 1, pulled: 0 })
    recordSyncOutcome({ status: 'stopped', reason: 'unknown_entity' })
    expect(getSyncState().syncStopped).toBe(true)
    expect(getSyncState().lastSyncedAt).not.toBeNull()

    const queryClient = new QueryClient()
    const memoryRouter = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(triggers.startSynchronizer).toHaveBeenCalled()
    })

    unmount()

    expect(getSyncState()).toEqual({
      lastSyncedAt: null,
      syncStopped: false,
    })
  })

  it('resets syncState when token changes to a different session', async () => {
    setSession('token-1', 'org-1')
    recordSyncOutcome({ status: 'synced', pushed: 1, pulled: 0 })
    expect(getSyncState().lastSyncedAt).not.toBeNull()

    const queryClient = new QueryClient()
    const memoryRouter = createMemoryRouter(buildRoutes(null), { initialEntries: ['/'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(triggers.startSynchronizer).toHaveBeenCalledTimes(1)
    })

    // Set stale state on first session
    recordSyncOutcome({ status: 'stopped', reason: 'unknown_entity' })
    expect(getSyncState().syncStopped).toBe(true)

    // Session changes
    setSession('token-2', 'org-2')

    await waitFor(() => {
      expect(getSyncState().lastSyncedAt).toBeNull()
      expect(getSyncState().syncStopped).toBe(false)
      expect(triggers.startSynchronizer).toHaveBeenCalledTimes(2)
    })
  })

  it('does not start the synchronizer on the sign-in screen', () => {
    clearSession()
    const queryClient = new QueryClient()
    const memoryRouter = createMemoryRouter(buildRoutes(null), { initialEntries: ['/ingreso'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={memoryRouter} />
      </QueryClientProvider>,
    )

    expect(triggers.startSynchronizer).not.toHaveBeenCalled()
  })
})

/**
 * D-T16.14: docs/07:125 hangs "Nueva entrada" off the Bitácora screen, and the
 * sheet's Parcela select is the screen's own `plots` prop — the tab has to be
 * the one that feeds it, the way the visits sheet is composed into
 * PlotsScreen (E8 D14). Without it the select is empty and every save dies in
 * "Selecciona una parcela.", which the scenario D e2e (E16 T7) would assert
 * as the documented airplane-mode flow.
 */
describe('the Bitácora tab feeds the Nueva entrada sheet its plots (D-T16.14)', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url =
          typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
        if (url.includes('/api/v1/me/tray')) {
          return new Response(JSON.stringify([]), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (/\/api\/v1\/farms\/[0-9a-f-]+\/plots/.test(url)) {
          return new Response(
            JSON.stringify([
              {
                id: '018f0c2a-0000-7000-8000-00000000d001',
                org_id: 'org-1',
                farm_id: '018f0c2a-0000-7000-8000-00000000f001',
                name: 'Lote El Ceibal',
                area_ha: 1.5,
              },
            ]),
            { status: 200, headers: { 'content-type': 'application/json' } },
          )
        }
        if (url.includes('/api/v1/farms')) {
          return new Response(
            JSON.stringify({
              items: [
                {
                  id: '018f0c2a-0000-7000-8000-00000000f001',
                  org_id: 'org-1',
                  name: 'Finca El Ceibal',
                  municipality_code: '05001',
                  location: 'POINT(-75.0 10.4)',
                  technician_id: null,
                },
              ],
              next_cursor: null,
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          )
        }
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
  })

  afterEach(() => {
    clearSession()
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('offers the org’s plots in the Nueva entrada sheet (docs/07:125)', async () => {
    setSession('token-prod', 'org-1')
    saveMeSeed('token-prod', {
      id: 'prod-1',
      phone: '+573001234568',
      email: null,
      full_name: 'Productor',
      memberships: [{ org_id: 'org-1', role: 'producer' }],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(buildRoutes(null), { initialEntries: ['/bitacora'] })

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    // Empty logbook: the header button and the empty-state CTA share the name.
    const newButtons = await screen.findAllByRole('button', { name: 'Nueva entrada' })
    fireEvent.click(newButtons[0] as HTMLButtonElement)

    const plotSelect = await screen.findByLabelText('Parcela')
    expect(within(plotSelect).getByRole('option', { name: 'Lote El Ceibal' })).toBeInTheDocument()
  })
})
