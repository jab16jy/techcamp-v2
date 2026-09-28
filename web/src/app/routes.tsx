import type { RouteObject } from 'react-router'
import { AppShell } from '../design-system/patterns/AppShell'
import { SignInScreen } from '../features/auth/containers/SignInScreen'
import { SignOutButton } from '../features/auth/components/SignOutButton'
import { requireAuthLoader } from '../features/auth/guard'
import { PlotsScreen } from '../features/plots/containers/PlotsScreen'
import { NotificationsCard } from '../features/push/components/NotificationsCard'
import { PlaceholderPage } from './PlaceholderPage'

const tabRoutes: RouteObject[] = [
  { index: true, element: <PlaceholderPage title="Inicio" /> },
  { path: 'alertas', element: <PlaceholderPage title="Alertas" /> },
  { path: 'bitacora', element: <PlaceholderPage title="Bitácora" /> },
  { path: 'parcelas', element: <PlotsScreen /> },
  {
    path: 'mas',
    element: (
      <PlaceholderPage title="Más">
        <SignOutButton />
        {/* docs/07's screen map: "Ajustes y notificaciones" hangs off `Más`. */}
        <NotificationsCard />
      </PlaceholderPage>
    ),
  },
]

/**
 * Nests `devRoute` (the /dev/ui catalog, at `dev/ui`) under the app shell only when one is
 * supplied — the catalog then gets the real bottom tab bar for free. `router.tsx` decides
 * whether to build one behind a direct `import.meta.env.DEV` check at module top level, so
 * Vite/Rollup can prove that branch dead and drop the catalog's chunk from the production
 * build entirely — passing the decision through this function's argument, instead of an
 * `isDev` flag read inside it, would hide that check behind a call boundary bundlers don't
 * reliably eliminate through.
 */
export function buildRoutes(devRoute: RouteObject | null): RouteObject[] {
  return [
    {
      path: '/',
      Component: AppShell,
      loader: requireAuthLoader,
      children: devRoute ? [...tabRoutes, devRoute] : tabRoutes,
    },
    { path: '/ingreso', Component: SignInScreen },
  ]
}
