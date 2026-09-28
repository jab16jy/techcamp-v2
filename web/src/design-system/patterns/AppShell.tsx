import { NavLink, Outlet } from 'react-router'
import { Toaster } from '../ui/toast'
import { cn } from '../ui/utils'
import { HomeIcon, BellIcon, NotebookIcon, MapIcon, MoreIcon, type IconProps } from '../ui/icons'
import type { ComponentType } from 'react'

interface Tab {
  to: string
  label: string
  Icon: ComponentType<IconProps>
  end?: boolean
}

const TABS: Tab[] = [
  { to: '/', label: 'Inicio', Icon: HomeIcon, end: true },
  { to: '/alertas', label: 'Alertas', Icon: BellIcon },
  { to: '/bitacora', label: 'Bitácora', Icon: NotebookIcon },
  { to: '/parcelas', label: 'Parcelas', Icon: MapIcon },
  { to: '/mas', label: 'Más', Icon: MoreIcon },
]

/** iOS-structure shell: content area plus a fixed bottom tab bar. Android back keeps working. */
export function AppShell() {
  return (
    <div className="flex min-h-dvh flex-col bg-surface text-text">
      <main className="mx-auto w-full max-w-md flex-1 pb-24">
        <Outlet />
      </main>
      <Toaster />
      <nav
        aria-label="Navegación principal"
        className="fixed inset-x-0 bottom-0 mx-auto flex max-w-md border-t border-text/10 bg-surface-raised"
      >
        {TABS.map(({ to, label, Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex min-h-12 flex-1 flex-col items-center justify-center gap-1 py-2 text-sm',
                isActive ? 'font-medium text-brand' : 'text-text-muted',
              )
            }
          >
            <Icon className="size-6" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
