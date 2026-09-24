import { useCallback, useState } from 'react'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { Button } from '../../../design-system/ui/button'
import { useApiResource } from '../../../lib/api/useApiResource'
import { useOrgId } from '../../../lib/api/session'
import { loadFarmsWithPlots } from '../api/plotsApi'
import { PlotsList } from '../components/PlotsList'

/** Plots tab (replaces the `PlaceholderPage`): the org's farms and each farm's plots. */
export function PlotsScreen() {
  const orgId = useOrgId()
  const [retryCount, setRetryCount] = useState(0)

  const load = useCallback(() => {
    if (!orgId) return Promise.resolve([])
    return loadFarmsWithPlots(orgId)
  }, [orgId])
  const state = useApiResource(load, [orgId, retryCount])

  return (
    <div className="px-0 pt-6">
      <h1 className="px-4 font-serif text-2xl">Parcelas</h1>
      <div className="mt-4">
        {!orgId && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="Elige una organización"
            description="Vuelve a ingresar para elegir la organización de tu cuenta."
          />
        )}
        {orgId && state.status === 'loading' && (
          <p className="px-4 text-base text-text-muted">Cargando parcelas…</p>
        )}
        {orgId && state.status === 'error' && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="No se pudieron cargar las parcelas"
            description="Revisa tu conexión e intenta de nuevo."
            action={
              <Button variant="secondary" onClick={() => setRetryCount((count) => count + 1)}>
                Reintentar
              </Button>
            }
          />
        )}
        {orgId && state.status === 'success' && state.data.length === 0 && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="Todavía no hay fincas"
            description="Las fincas y parcelas de tu organización aparecerán aquí."
          />
        )}
        {orgId && state.status === 'success' && state.data.length > 0 && (
          <PlotsList farms={state.data} />
        )}
      </div>
    </div>
  )
}
