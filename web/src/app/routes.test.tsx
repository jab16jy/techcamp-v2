import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as triggers from '../lib/sync/triggers'
import * as db from '../lib/db/db'
import { clearSession, setSession } from '../lib/api/session'
import { getSyncState, recordSyncOutcome } from '../lib/sync/syncState'
import { buildRoutes } from './routes'

describe('buildRoutes', () => {
  it('excludes the dev-only catalog route when none is supplied', () => {
    const routes = buildRoutes(null)

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(false)
  })

  it('nests the supplied dev-only catalog route under the app shell', () => {
    const routes = buildRoutes({ path: 'dev/ui', element: null })

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(true)
  })

  it('always includes the five tabs at the root', () => {
    const routes = buildRoutes(null)

    expect(routes[0].path).toBe('/')
    expect(routes[0].children?.length).toBe(5)
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
