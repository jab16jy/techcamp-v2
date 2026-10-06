import { useMemo } from 'react'
import { Link } from 'react-router'
import { Button, buttonVariants } from '../../../design-system/ui/button'
import { MapIcon } from '../../../design-system/ui/icons'
import { useActivePlotId } from '../../../lib/api/activePlot'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useActiveOrgRole } from '../../../lib/api/me'
import { useOrgId } from '../../../lib/api/session'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import {
  isMonthWithoutReport,
  previousMonthInBogota,
  useOrgIndicators,
  usePlotIndicators,
} from '../api/indicatorsApi'
import { OrgIndicatorsSection, PlotIndicatorsSection } from '../components/IndicatorsSection'

/** D-T0.10: the organization's indicators are `403` for any other member. */
const SEES_ORG_INDICATORS = new Set(['owner', 'technician'])

/**
 * "Indicadores de tecnificación" (Más → Indicadores, docs/07:145-148; E11
 * D-T0.13, RF-13): the active plot's adoption month, plus the organization's own
 * figures for the same month for the roles allowed to read them.
 *
 * Which plot: the one this device remembers as active (docs/07 Inicio item 1,
 * `lib/api/activePlot`). Read from the shared UI store rather than resolved
 * again here, because docs/07 §Estructura forbids one feature importing another
 * and the resolution rules live with the home screen; with no plot remembered
 * the screen says so and links to Parcelas rather than firing farms and plots
 * requests a 3G phone does not need for a heading.
 */
export function IndicatorsScreen() {
  const orgId = useOrgId()
  const role = useActiveOrgRole()
  const plotId = useActivePlotId(orgId)
  /**
   * Fixed for the screen's lifetime: the chosen month only turns over at Bogotá
   * midnight, and re-deriving it per render would let a screen left open across
   * that midnight ask for a month the rows on screen were not read from.
   */
  const month = useMemo(() => previousMonthInBogota(), [])

  /**
   * `null` for a role that may not read them, so the request never leaves the
   * device: the server would answer `403`, and a refused read on a metered
   * connection is a round trip spent to learn nothing.
   */
  const canSeeOrg = role !== null && SEES_ORG_INDICATORS.has(role)
  const orgQuery = useOrgIndicators(canSeeOrg ? orgId : null, month)
  const plotQuery = usePlotIndicators(orgId, plotId, month)

  /**
   * The data, not the success flag (D-T0.10): a refetch that fails with no
   * connection leaves the restored persisted month in `data`, and that cached
   * month IS the offline answer.
   */
  const plotIndicators = plotQuery.data ?? null
  /**
   * A month the job never stored is a `404`, which is the section's own absent
   * state rather than a failure to report (docs/07:147). `isMonthWithoutReport`
   * is what keeps the cross-organization `404` out of this branch.
   */
  const plotAbsent = isMonthWithoutReport(plotQuery.error)
  const plotFailed = plotQuery.isError && !plotAbsent && plotIndicators === null
  const plotPending = plotQuery.isPending && !plotFailed

  const orgIndicators = orgQuery.data ?? null
  const orgFailed = orgQuery.isError && orgQuery.data === undefined

  if (orgId === null) {
    return (
      <EmptyState
        icon={<MapIcon className="size-10" />}
        title="Elige una organización"
        description="Vuelve a ingresar para elegir la organización de tu cuenta."
      />
    )
  }

  return (
    <div className="px-0 pt-6">
      <div className="px-4">
        <h1 className="font-serif text-2xl">Indicadores de tecnificación</h1>
      </div>

      {plotId === null && (
        <EmptyState
          icon={<MapIcon className="size-10" />}
          title="No hay una parcela activa"
          description="Abre una parcela para ver sus indicadores de tecnificación."
          action={
            <Link to="/parcelas" className={buttonVariants({ variant: 'primary' })}>
              Ir a Parcelas
            </Link>
          }
        />
      )}

      {plotId !== null && plotPending && (
        <p className="mt-4 px-4 text-base text-text-muted">Cargando indicadores…</p>
      )}

      {plotFailed && (
        <EmptyState
          icon={<MapIcon className="size-10" />}
          title="No se pudieron cargar los indicadores"
          description={describeApiError(plotQuery.error)}
          action={
            <Button variant="secondary" onClick={() => void plotQuery.refetch()}>
              Reintentar
            </Button>
          }
        />
      )}

      {plotId !== null && !plotPending && !plotFailed && (
        <PlotIndicatorsSection month={month} indicators={plotIndicators} />
      )}

      {canSeeOrg && orgQuery.isPending && (
        <p className="mt-6 px-4 text-base text-text-muted">Cargando los indicadores de la organización…</p>
      )}

      {canSeeOrg && orgFailed && (
        <p className="mt-6 px-4 text-base text-text-muted">
          No se pudieron cargar los indicadores de la organización.
        </p>
      )}

      {canSeeOrg && !orgQuery.isPending && !orgFailed && (
        <OrgIndicatorsSection month={month} indicators={orgIndicators} />
      )}
    </div>
  )
}
