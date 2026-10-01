import { Link } from 'react-router'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { buttonVariants } from '../../../design-system/ui/button'
import { Button } from '../../../design-system/ui/button'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { cycleLine, decisionFor } from '../api/decisionCopy'
import { useActivePlot } from '../api/useActivePlot'
import { usePlotStatus } from '../api/usePlotStatus'
import { DecisionCard } from '../components/DecisionCard'
import { PlotHeader } from '../components/PlotHeader'

/**
 * Inicio, the most important screen (docs/07 §Mapa de pantallas): items 1 and 2,
 * the active plot with its crop and the day's decision. Items 3–5 (open alerts,
 * soil moisture and forecast, sync and nodes) are added by T5b on top of this
 * same screen; `digital_adoption_index` stays unrendered until E11 (D-T0.2).
 */
export function PlotStatusScreen() {
  const orgId = useOrgId()
  const activePlot = useActivePlot()
  const plotId = activePlot.kind === 'ready' ? activePlot.plot.id : null
  const statusQuery = usePlotStatus(orgId, plotId)

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
          <PlotHeader
            plotName={activePlot.plot.name}
            cycleLine={statusQuery.isSuccess ? cycleLine(statusQuery.data.active_cycle) : null}
          />
          {statusQuery.isPending && (
            <p className="mt-4 px-4 text-base text-text-muted">Cargando el estado de la parcela…</p>
          )}
          {statusQuery.isError && (
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
          {statusQuery.isSuccess && <DecisionCard decision={decisionFor(statusQuery.data)} />}
        </>
      )}
    </div>
  )
}
