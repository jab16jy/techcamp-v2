import { Link } from 'react-router'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { DropletIcon, MapIcon } from '../../../design-system/ui/icons'
import { buttonVariants } from '../../../design-system/ui/button'
import { useActivePlotId } from '../../../lib/api/activePlot'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { riskEventLines } from '../api/riskCopy'
import { usePlotRisk } from '../api/usePlotRisk'
import { RiskCard } from '../components/RiskCard'

/**
 * Riesgo climático (docs/07 §Mapa de pantallas: `home → risk`): the risk the
 * model predicts for the active plot.
 *
 * The screen answers three things and no more: what could happen, how likely it
 * is, and where the number comes from. It does not invent a severity for a
 * version without calibrated cuts (D-T9.2, D-DH.12), and it says plainly when
 * the plot has no prediction yet.
 *
 * Which plot: the one this device remembers as active (docs/07 Inicio item 1),
 * read from the shared UI store rather than resolved again here, because
 * docs/07 §Estructura forbids one feature importing another and the resolution
 * rules live with the home screen.
 */
export function RiskScreen() {
  const orgId = useOrgId()
  const plotId = useActivePlotId(orgId)
  const riskQuery = usePlotRisk(orgId, plotId)

  const predictions = riskQuery.data ?? []
  const events = riskEventLines(predictions)

  if (orgId === null) {
    return (
      <EmptyState
        icon={<MapIcon className="size-10" />}
        title="Elige una organización"
        description="Vuelve a ingresar para elegir la organización de tu cuenta."
      />
    )
  }

  if (plotId === null) {
    return (
      <EmptyState
        icon={<MapIcon className="size-10" />}
        title="Sin parcela activa"
        description="Abre una parcela para ver el riesgo climático que el modelo predice para ella."
        action={
          <Link to="/parcelas" className={buttonVariants({ variant: 'primary' })}>
            Ir a Parcelas
          </Link>
        }
      />
    )
  }

  return (
    <div className="px-4 pt-6 pb-8">
      <header>
        <h1 className="font-serif text-2xl">Riesgo climático</h1>
        <p className="mt-1 text-base text-text-muted">La parcela activa</p>
      </header>

      {riskQuery.isPending && (
        <p className="mt-4 text-base text-text-muted">Cargando el riesgo climático…</p>
      )}

      {riskQuery.isError && predictions.length === 0 && (
        <EmptyState
          icon={<DropletIcon className="size-10" />}
          title="No se pudo cargar el riesgo climático"
          description={describeApiError(riskQuery.error)}
        />
      )}

      {!riskQuery.isPending && !riskQuery.isError && events.length === 0 && (
        <EmptyState
          icon={<DropletIcon className="size-10" />}
          title="Sin predicciones de riesgo"
          description="Esta parcela todavía no tiene una predicción de riesgo registrada. El modelo calcula el riesgo una vez al día, por celda."
        />
      )}

      {events.length > 0 && (
        <div className="mt-4 space-y-3">
          {events.map((event) => (
            <RiskCard key={event.event} event={event} />
          ))}
        </div>
      )}
    </div>
  )
}
