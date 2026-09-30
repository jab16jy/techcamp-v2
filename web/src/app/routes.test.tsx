import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
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

  it('includes the five tabs and the estado route at the root', () => {
    const routes = buildRoutes(null)

    expect(routes[0].path).toBe('/')
    expect(routes[0].children?.some((route) => route.index === true)).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'estado')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'alertas')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'bitacora')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'parcelas')).toBe(true)
    expect(routes[0].children?.some((route) => route.path === 'mas')).toBe(true)
    expect(routes[0].children?.length).toBe(6)
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

    await waitFor(() => expect(screen.getByText(/Volver a la bandeja/i)).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Sin parcelas')).toBeInTheDocument())
    // Negative assertion: TrayScreen is not on /estado
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
