import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { minutesSince } from '../../../design-system/components/format'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { OfflineBanner } from '../../../design-system/patterns/OfflineBanner'
import { MapIcon } from '../../../design-system/ui/icons'
import { Button } from '../../../design-system/ui/button'
import { setActivePlotId } from '../../../lib/api/activePlot'
import { describeApiError } from '../../../lib/api/errorCopy'
import { usePlotsByFarm, type PlotView } from '../../../lib/api/farms'
import { setOrgId, useOrgId } from '../../../lib/api/session'
import { usePendingCount } from '../../../lib/db/live'
import { useOnlineStatus, useSyncState } from '../../../lib/sync/syncState'
import { useTray, type TrayItem } from '../api/useTray'
import { TrayList } from '../components/TrayList'
import { NewVisitSheet } from './NewVisitSheet'

/**
 * Container for the technician tray screen (docs/07 §Mapa de pantallas "Bandeja del técnico"):
 * lists assigned farms across organizations, open alerts (critical named), and last visit.
 * Tapping a plot switches to that plot's org if different, remembers it, and opens /estado (D-T6.1).
 */
export function TrayScreen() {
  const orgId = useOrgId()
  const navigate = useNavigate()
  const trayQuery = useTray(orgId)
  /**
   * The data, not the success flag: a refetch that fails with no connection
   * leaves the restored persisted state in `data` with `isError` set, and that
   * cached state IS the offline answer (D-T0.10).
   */
  const trayItems = trayQuery.data ?? null

  const [openedFarmIds, setOpenedFarmIds] = useState<string[]>([])
  const [visitingFarm, setVisitingFarm] = useState<TrayItem['farm'] | null>(null)

  const farmIdsToLoad = useMemo(
    () => Array.from(new Set([...openedFarmIds, ...(visitingFarm ? [visitingFarm.id] : [])])),
    [openedFarmIds, visitingFarm],
  )
  const plotsQueries = usePlotsByFarm(farmIdsToLoad)
  const plotsByFarmId = useMemo(
    () => new Map(farmIdsToLoad.map((id, index) => [id, plotsQueries[index]])),
    [farmIdsToLoad, plotsQueries],
  )

  const online = useOnlineStatus()
  const pendingCount = usePendingCount()
  const { syncStopped } = useSyncState()
  const lastDataMinutesAgo = useMemo(
    () =>
      trayQuery.dataUpdatedAt
        ? minutesSince(new Date(trayQuery.dataUpdatedAt).toISOString())
        : null,
    [trayQuery.dataUpdatedAt],
  )

  const handleToggleOpen = (farmId: string) => {
    setOpenedFarmIds((prev) =>
      prev.includes(farmId) ? prev.filter((id) => id !== farmId) : [...prev, farmId],
    )
  }

  const handleSelectPlot = (farm: TrayItem['farm'], plot: PlotView) => {
    if (farm.org_id !== orgId) {
      setOrgId(farm.org_id)
    }
    setActivePlotId(farm.org_id, plot.id)
    navigate('/estado')
  }

  const handleAddVisit = (farm: TrayItem['farm']) => {
    setVisitingFarm(farm)
    if (!openedFarmIds.includes(farm.id)) {
      setOpenedFarmIds((prev) => [...prev, farm.id])
    }
  }

  return (
    <div className="px-0 pt-0">
      {(!online || syncStopped) && (
        <OfflineBanner
          online={online}
          pendingCount={pendingCount}
          lastDataMinutesAgo={lastDataMinutesAgo}
          syncStopped={syncStopped}
        />
      )}

      <div className="flex items-center justify-between gap-2 px-4 pt-6">
        <h1 className="font-serif text-2xl">Bandeja del técnico</h1>
      </div>

      <div className="mt-4">
        {orgId === null && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="Elige una organización"
            description="Vuelve a ingresar para elegir la organización de tu cuenta."
          />
        )}

        {orgId !== null && trayQuery.isPending && (
          <p className="px-4 text-base text-text-muted">Cargando bandeja…</p>
        )}

        {orgId !== null && trayQuery.isError && trayItems === null && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="No se pudo cargar la bandeja"
            description={describeApiError(trayQuery.error)}
            action={
              <Button variant="secondary" onClick={() => void trayQuery.refetch()}>
                Reintentar
              </Button>
            }
          />
        )}

        {trayItems !== null && trayItems.length === 0 && (
          <EmptyState
            icon={<MapIcon className="size-10" />}
            title="Sin fincas asignadas"
            description="No tienes fincas asignadas como técnico en este momento."
          />
        )}

        {trayItems !== null && trayItems.length > 0 && (
          <TrayList
            rows={trayItems.map((item) => ({
              item,
              isOpen: openedFarmIds.includes(item.farm.id),
              plotsQuery: plotsByFarmId.get(item.farm.id),
            }))}
            onToggleOpen={handleToggleOpen}
            onSelectPlot={handleSelectPlot}
            onAddVisit={handleAddVisit}
          />
        )}
      </div>

      {visitingFarm && (
        <NewVisitSheet
          open
          onOpenChange={(open) => {
            if (!open) setVisitingFarm(null)
          }}
          farm={visitingFarm}
          plots={plotsByFarmId.get(visitingFarm.id)?.data ?? []}
        />
      )}
    </div>
  )
}
