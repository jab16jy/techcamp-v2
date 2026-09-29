import { lazy, Suspense } from 'react'
import type { RouteObject } from 'react-router'
import { AuthenticatedShell } from './AuthenticatedShell'
import { SignInScreen } from '../features/auth/containers/SignInScreen'
import { SignOutButton } from '../features/auth/components/SignOutButton'
import { requireAuthLoader } from '../features/auth/guard'
import { PlotsScreen } from '../features/plots/containers/PlotsScreen'
import { NotificationsCard } from '../features/push/components/NotificationsCard'
import { useActiveOrgRole } from '../lib/api/me'
import { PlaceholderPage } from './PlaceholderPage'

// React.lazy keeps Dexie and logbook/visits out of the initial bundle (RNF-02 ≤ 200 kB).
// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const LogbookScreen = lazy(() =>
  import('../features/logbook/containers/LogbookScreen').then((module) => ({
    default: module.LogbookScreen,
  })),
)

// eslint-disable-next-line react-refresh/only-export-components -- route tab wrapper lives with routes
function BitacoraTab() {
  return (
    <Suspense fallback={null}>
      <LogbookScreen />
    </Suspense>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const NewVisitSheet = lazy(() =>
  import('../features/visits/containers/NewVisitSheet').then((module) => ({
    default: module.NewVisitSheet,
  })),
)

/** Parcelas tab wrapper: composes PlotsScreen with the extension visit sheet (E8 D14). */
// eslint-disable-next-line react-refresh/only-export-components -- route tab wrapper lives with routes
function PlotsTab() {
  const role = useActiveOrgRole()
  return (
    <PlotsScreen
      callerRole={role}
      renderNewVisitSheet={({ farm, plots, open, onOpenChange }) => (
        <Suspense fallback={null}>
          <NewVisitSheet
            open={open}
            onOpenChange={onOpenChange}
            farm={farm}
            plots={plots}
          />
        </Suspense>
      )}
    />
  )
}

const tabRoutes: RouteObject[] = [
  { index: true, element: <PlaceholderPage title="Inicio" /> },
  { path: 'alertas', element: <PlaceholderPage title="Alertas" /> },
  { path: 'bitacora', element: <BitacoraTab /> },
  { path: 'parcelas', element: <PlotsTab /> },
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
      Component: AuthenticatedShell,
      loader: requireAuthLoader,
      children: devRoute ? [...tabRoutes, devRoute] : tabRoutes,
    },
    { path: '/ingreso', Component: SignInScreen },
  ]
}
