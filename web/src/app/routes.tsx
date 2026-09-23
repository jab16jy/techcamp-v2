import type { RouteObject } from 'react-router'
import { AppShell } from '../design-system/patterns/AppShell'
import { PlaceholderPage } from './PlaceholderPage'

const appShellRoute: RouteObject = {
  path: '/',
  Component: AppShell,
  children: [
    { index: true, element: <PlaceholderPage title="Inicio" /> },
    { path: 'alertas', element: <PlaceholderPage title="Alertas" /> },
    { path: 'bitacora', element: <PlaceholderPage title="Bitácora" /> },
    { path: 'parcelas', element: <PlaceholderPage title="Parcelas" /> },
    { path: 'mas', element: <PlaceholderPage title="Más" /> },
  ],
}

/**
 * Appends `devRoute` (the /dev/ui catalog) only when one is supplied. `router.tsx` decides
 * whether to build one behind a direct `import.meta.env.DEV` check at module top level, so
 * Vite/Rollup can prove that branch dead and drop the catalog's chunk from the production
 * build entirely — passing the decision through this function's argument, instead of an
 * `isDev` flag read inside it, would hide that check behind a call boundary bundlers don't
 * reliably eliminate through.
 */
export function buildRoutes(devRoute: RouteObject | null): RouteObject[] {
  return devRoute ? [appShellRoute, devRoute] : [appShellRoute]
}
