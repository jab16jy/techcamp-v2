import { lazy, Suspense } from 'react'
import { Link, type RouteObject } from 'react-router'
import { AuthenticatedShell } from './AuthenticatedShell'
import { SignInScreen } from '../features/auth/containers/SignInScreen'
import { SignOutButton } from '../features/auth/components/SignOutButton'
import { requireAuthLoader } from '../features/auth/guard'
import { PlotsScreen } from '../features/plots/containers/PlotsScreen'
import { NotificationsCard } from '../features/push/components/NotificationsCard'
import { buttonVariants } from '../design-system/ui/button'
import { ChevronDownIcon } from '../design-system/ui/icons'
import { cn } from '../design-system/ui/utils'
import { useActiveOrgRole, useMe } from '../lib/api/me'
import { PlaceholderPage } from './PlaceholderPage'

// React.lazy keeps Dexie and logbook/visits out of the initial bundle (RNF-02 ≤ 200 kB).
// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const LogbookScreen = lazy(() =>
  import('../features/logbook/containers/LogbookScreen').then((module) => ({
    default: module.LogbookScreen,
  })),
)

// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const PlotStatusScreen = lazy(() =>
  import('../features/plot-status/containers/PlotStatusScreen').then((module) => ({
    default: module.PlotStatusScreen,
  })),
)

// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const TrayScreen = lazy(() =>
  import('../features/visits/containers/TrayScreen').then((module) => ({
    default: module.TrayScreen,
  })),
)

// eslint-disable-next-line react-refresh/only-export-components -- lazy component lives with routes
const IndicatorsScreen = lazy(() =>
  import('../features/metrics/containers/IndicatorsScreen').then((module) => ({
    default: module.IndicatorsScreen,
  })),
)

/**
 * Inicio tab wrapper: renders TrayScreen when the caller has the technician role
 * in the active organization, and PlotStatusScreen for any other role (D-T0.9, docs/07).
 * While the /me query is pending (no data yet), renders null to prevent flashing
 * PlotStatusScreen and firing its queries for technicians.
 *
 * `isPending`, NOT `isLoading`: in v5 `isLoading === isPending && isFetching`, and an
 * uncached `/me` whose request cannot leave the device is PAUSED — pending, not
 * fetching. Waiting on `isLoading` picked the plot status screen for a technician the
 * moment the phone lost signal, flashing it and firing its queries (T6 no-status-flash
 * rule, #226). A query that ERRORED is not pending, so the status screen stays the
 * fallback below.
 */
// eslint-disable-next-line react-refresh/only-export-components -- route tab wrapper lives with routes
function InicioTab() {
  const meQuery = useMe()
  const role = useActiveOrgRole()

  if (meQuery.isPending) {
    return null
  }

  return (
    <Suspense fallback={null}>
      {role === 'technician' ? <TrayScreen /> : <PlotStatusScreen />}
    </Suspense>
  )
}

/**
 * Estado child route (D-T6.1): renders PlotStatusScreen with a link back to Inicio
 * ("Volver a la bandeja"), so tapping a plot from the technician tray opens its status.
 */
// eslint-disable-next-line react-refresh/only-export-components -- route wrapper lives with routes
function EstadoRoute() {
  return (
    <Suspense fallback={null}>
      <div className="px-4 pt-4">
        <Link
          to="/"
          className={cn(
            buttonVariants({ variant: 'ghost' }),
            'gap-1 px-2 text-text-muted hover:text-text',
          )}
        >
          <ChevronDownIcon className="size-5 rotate-90" aria-hidden="true" />
          Volver a la bandeja
        </Link>
      </div>
      <PlotStatusScreen />
    </Suspense>
  )
}

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

/**
 * Más tab wrapper (replaces the `PlaceholderPage`, E11 D-T0.13): the entries
 * docs/07's screen map hangs off `Más`, starting with the technification
 * indicators, plus the sign-out action and the notification switch that were
 * already here. The other three entries in the map are still placeholders, so
 * this screen does not claim them.
 *
 * A grouped row with a trailing chevron rather than a button: it navigates to
 * another screen in this tab, which is what a list row is for.
 */
// eslint-disable-next-line react-refresh/only-export-components -- route tab wrapper lives with routes
function MasTab() {
  return (
    <div className="px-0 pt-6">
      <div className="px-4">
        <h1 className="font-serif text-2xl">Más</h1>
      </div>
      <ul className="mx-4 mt-4 divide-y divide-text/10 rounded-lg bg-surface-raised">
        <li>
          <Link
            to="/mas/indicadores"
            className="flex min-h-12 items-center justify-between gap-3 px-4 py-3"
          >
            <span className="text-base text-text">Indicadores de tecnificación</span>
            <ChevronDownIcon
              className="size-5 shrink-0 -rotate-90 text-text-muted"
              aria-hidden="true"
            />
          </Link>
        </li>
      </ul>
      <div className="px-4">
        <SignOutButton />
        {/* docs/07's screen map: "Ajustes y notificaciones" hangs off `Más`. */}
        <NotificationsCard />
      </div>
    </div>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- route tab wrapper lives with routes
function IndicatorsRoute() {
  return (
    <Suspense fallback={null}>
      <IndicatorsScreen />
    </Suspense>
  )
}

const tabRoutes: RouteObject[] = [
  { index: true, element: <InicioTab /> },
  { path: 'estado', element: <EstadoRoute /> },
  { path: 'alertas', element: <PlaceholderPage title="Alertas" /> },
  { path: 'bitacora', element: <BitacoraTab /> },
  { path: 'parcelas', element: <PlotsTab /> },
  { path: 'mas', element: <MasTab /> },
  { path: 'mas/indicadores', element: <IndicatorsRoute /> },
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
