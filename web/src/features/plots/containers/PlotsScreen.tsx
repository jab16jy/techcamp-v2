import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { Button } from '../../../design-system/ui/button'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { useFarms, usePlotsByFarm } from '../api/plotsApi'
import { PlotsList } from '../components/PlotsList'

/** Plots tab (replaces the `PlaceholderPage`): the org's farms and each farm's plots. */
export function PlotsScreen() {
  const orgId = useOrgId()
  const farmsQuery = useFarms(orgId)
  const farmIds = farmsQuery.data?.farms.map((farm) => farm.id) ?? []
  const plotsQueries = usePlotsByFarm(farmIds)

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
        {orgId && farmsQuery.isPending && (
          <p className="px-4 text-base text-text-muted">Cargando parcelas…</p>
        )}
        {orgId && farmsQuery.isError && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="No se pudieron cargar las parcelas"
            description={describeApiError(farmsQuery.error)}
            action={
              <Button variant="secondary" onClick={() => farmsQuery.refetch()}>
                Reintentar
              </Button>
            }
          />
        )}
        {orgId && farmsQuery.isSuccess && farmsQuery.data.farms.length === 0 && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="Todavía no hay fincas"
            description="Las fincas y parcelas de tu organización aparecerán aquí."
          />
        )}
        {orgId && farmsQuery.isSuccess && farmsQuery.data.farms.length > 0 && (
          <>
            <PlotsList
              rows={farmsQuery.data.farms.map((farm, index) => ({
                farm,
                plotsQuery: plotsQueries[index],
              }))}
            />
            {farmsQuery.data.hasMore && (
              <p className="px-4 text-base text-text-muted">
                Tu organización tiene más fincas de las que se muestran aquí.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
