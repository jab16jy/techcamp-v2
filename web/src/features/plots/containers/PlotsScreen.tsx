import { useState } from 'react'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { Button } from '../../../design-system/ui/button'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { useFarms, usePlotsByFarm, type PlotView } from '../api/plotsApi'
import { PlotsList } from '../components/PlotsList'
import { CreateFarmSheet } from './CreateFarmSheet'
import { CreatePlotSheet } from './CreatePlotSheet'
import { PlotDetailSheet } from './PlotDetailSheet'

/** Plots tab (replaces the `PlaceholderPage`): the org's farms and each farm's plots,
 * plus farm/plot creation (T8, docs/07: the tab's own empty-state CTA and list). */
export function PlotsScreen() {
  const orgId = useOrgId()
  const farmsQuery = useFarms(orgId)
  const farmIds = farmsQuery.data?.farms.map((farm) => farm.id) ?? []
  const plotsQueries = usePlotsByFarm(farmIds)
  const [creatingFarm, setCreatingFarm] = useState(false)
  const [creatingPlotForFarmId, setCreatingPlotForFarmId] = useState<string | null>(null)
  const [selectedPlot, setSelectedPlot] = useState<PlotView | null>(null)

  return (
    <div className="px-0 pt-6">
      <div className="flex items-center justify-between gap-2 px-4">
        <h1 className="font-serif text-2xl">Parcelas</h1>
        {orgId && (
          <Button variant="secondary" onClick={() => setCreatingFarm(true)}>
            Nueva finca
          </Button>
        )}
      </div>
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
            action={
              <Button variant="primary" onClick={() => setCreatingFarm(true)}>
                Crear finca
              </Button>
            }
          />
        )}
        {orgId && farmsQuery.isSuccess && farmsQuery.data.farms.length > 0 && (
          <>
            <PlotsList
              rows={farmsQuery.data.farms.map((farm, index) => ({
                farm,
                plotsQuery: plotsQueries[index],
              }))}
              onAddPlot={setCreatingPlotForFarmId}
              onSelectPlot={setSelectedPlot}
            />
            {farmsQuery.data.hasMore && (
              <p className="px-4 text-base text-text-muted">
                Tu organización tiene más fincas de las que se muestran aquí.
              </p>
            )}
          </>
        )}
      </div>
      <CreateFarmSheet open={creatingFarm} onOpenChange={setCreatingFarm} />
      {creatingPlotForFarmId && (
        <CreatePlotSheet
          open
          onOpenChange={(open) => !open && setCreatingPlotForFarmId(null)}
          farmId={creatingPlotForFarmId}
        />
      )}
      {selectedPlot && (
        <PlotDetailSheet
          open
          onOpenChange={(open) => !open && setSelectedPlot(null)}
          plotId={selectedPlot.id}
          farmId={selectedPlot.farm_id}
          plotName={selectedPlot.name}
        />
      )}
    </div>
  )
}
