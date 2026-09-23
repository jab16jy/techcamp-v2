import { createBrowserRouter, type RouteObject } from 'react-router'
import { lazy, Suspense } from 'react'
import { buildRoutes } from './routes'

let devRoute: RouteObject | null = null

// Direct top-level check on the literal Vite replaces at build time: in production this
// whole block is dead code, so Rollup drops the dynamic import (and its chunk) entirely
// instead of merely skipping it at runtime.
if (import.meta.env.DEV) {
  const DevUiCatalog = lazy(() => import('../dev-ui/DevUiCatalog'))
  devRoute = {
    path: '/dev/ui',
    element: (
      <Suspense fallback={null}>
        <DevUiCatalog />
      </Suspense>
    ),
  }
}

export const router = createBrowserRouter(buildRoutes(devRoute))
