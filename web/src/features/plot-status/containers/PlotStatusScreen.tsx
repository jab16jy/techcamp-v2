import { useCallback, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { minutesSince } from '../../../design-system/components/format'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { Button, buttonVariants } from '../../../design-system/ui/button'
import { useFarmEvents, type FarmEvent } from '../../../lib/api/useFarmEvents'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { usePendingCount } from '../../../lib/db/live'
import { useOnlineStatus, useSyncState } from '../../../lib/sync/syncState'
import { cycleLine, decisionFor } from '../api/decisionCopy'
import { useActivePlot, useForgetMissingPlot } from '../api/useActivePlot'
import { plotStatusKey, usePlotStatus } from '../api/usePlotStatus'
import { DecisionCard } from '../components/DecisionCard'
import { PlotHeader } from '../components/PlotHeader'
import {
  ConnectionBanner,
  OpenAlerts,
  SoilMoistureAndForecast,
  SyncAndNodes,
} from '../components/StatusSections'

/**
 * Inicio, the most important screen (docs/07 §Mapa de pantallas): the active
 * plot with its crop, the day's decision, the open alerts, the soil moisture and
 * three-day forecast, and the sync and node state.
 *
 * `digital_adoption_index` is rendered as the discreet line docs/07:152 asks
 * for, right under the crop line, and nothing at all when it is `null` — a tile
 * for a value that never arrives is a lie about the data.
 */
export function PlotStatusScreen() {
  const orgId = useOrgId()
  const queryClient = useQueryClient()
  const activePlot = useActivePlot()
  const plotId = activePlot.kind === 'ready' ? activePlot.plotId : null
  const statusQuery = usePlotStatus(orgId, plotId)
  /**
   * The data, not the success flag: a refetch that fails with no connection
   * leaves the restored persisted state in `data` with `isError` set, and that
   * cached state IS the offline answer (D-T0.10). Reading `isSuccess` instead
   * would blank the screen the moment the phone loses signal.
   */
  const status = statusQuery.data ?? null

  useForgetMissingPlot(orgId, plotId, statusQuery.error)

  const online = useOnlineStatus()
  const pendingCount = usePendingCount()
  const { syncStopped } = useSyncState()
  const lastDataMinutesAgo = useMemo(
    () => minutesSince(status?.latest.at ?? null),
    [status?.latest.at],
  )

  /**
   * SSE invalidates the affected query (docs/07 §Flujo de datos y offline: "SSE
   * invalida o actualiza las consultas afectadas"). The farm's stream also
   * carries other plots' readings, so an event naming another plot leaves this
   * one alone instead of refetching for nothing on a 3G phone.
   */
  const onFarmEvent = useCallback(
    (event: FarmEvent) => {
      if (orgId === null || plotId === null) return
      if (event.event === 'alert.opened' || event.event === 'alert.updated') {
        void queryClient.invalidateQueries({ queryKey: plotStatusKey(orgId, plotId) })
        return
      }
      if (event.event === 'reading' || event.event === 'node.status') {
        const data = event.data
        if (typeof data !== 'object' || data === null) return
        const plot_id = (data as { plot_id?: unknown }).plot_id
        if (plot_id !== undefined && plot_id !== plotId) return
        void queryClient.invalidateQueries({ queryKey: plotStatusKey(orgId, plotId) })
      }
    },
    [orgId, plotId, queryClient],
  )
  useFarmEvents(status?.plot.farm_id ?? null, onFarmEvent)

  return (
    <div>
      {activePlot.kind === 'no-org' && (
        <EmptyState
          icon={<MapIcon className="size-10" />}
          title="Elige una organización"
          description="Vuelve a ingresar para elegir la organización de tu cuenta."
        />
      )}

      {activePlot.kind === 'loading' && (
        <p className="px-4 pt-6 text-base text-text-muted">Cargando parcelas…</p>
      )}

      {activePlot.kind === 'error' && (
        <EmptyState
          icon={<MapIcon className="size-10" />}
          title="No se pudieron cargar las parcelas"
          description={describeApiError(activePlot.error)}
          action={
            <Button variant="secondary" onClick={activePlot.retry}>
              Reintentar
            </Button>
          }
        />
      )}

      {activePlot.kind === 'no-plots' && (
        <EmptyState
          icon={<MapIcon className="size-10" />}
          title="Sin parcelas"
          description="Esta organización todavía no tiene parcelas. Crea una y aquí verás la decisión de cada día."
          action={
            <Link to="/parcelas" className={buttonVariants({ variant: 'primary' })}>
              Ir a Parcelas
            </Link>
          }
        />
      )}

      {activePlot.kind === 'ready' && (
        <>
          <ConnectionBanner
            online={online}
            pendingCount={pendingCount}
            lastDataMinutesAgo={lastDataMinutesAgo}
            syncStopped={syncStopped}
          />
          <PlotHeader
            plotName={status?.plot.name ?? activePlot.plotName ?? ''}
            cycleLine={status ? cycleLine(status.active_cycle) : null}
            // `?? null` because a persisted `/status` from a build older than T8
            // restores without the field at all, and `undefined` is not the
            // absence the header reads for.
            adoptionIndex={status?.digital_adoption_index ?? null}
          />
          {statusQuery.isPending && (
            <p className="mt-4 px-4 text-base text-text-muted">Cargando el estado de la parcela…</p>
          )}
          {statusQuery.isError && status === null && (
            <EmptyState
              icon={<MapIcon className="size-10" />}
              title="No se pudo cargar el estado de la parcela"
              description={describeApiError(statusQuery.error)}
              action={
                <Button variant="secondary" onClick={() => void statusQuery.refetch()}>
                  Reintentar
                </Button>
              }
            />
          )}
          {status && (
            <>
              <DecisionCard decision={decisionFor(status)} />
              <div className="mt-6">
                <h2 className="px-4 text-lg font-semibold">Alertas abiertas</h2>
                <div className="mt-2">
                  <OpenAlerts alerts={status.open_alerts} />
                </div>
              </div>
              <SoilMoistureAndForecast
                latest={status.latest}
                waterBalance={status.water_balance}
                weather={status.weather_next_3d}
              />
              <SyncAndNodes
                nodes={status.nodes}
                lastDataMinutesAgo={lastDataMinutesAgo}
                online={online}
                pendingCount={pendingCount}
                syncStopped={syncStopped}
              />
            </>
          )}
        </>
      )}
    </div>
  )
}
