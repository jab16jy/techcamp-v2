import { useState } from 'react'
import { Button } from '../../../design-system/ui/button'
import { ApiError } from '../../../lib/api/client'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import type { Role } from '../../../lib/api/me'
import { useCrops } from '../../plots/api/plotsApi'
import {
  usePlotBaseline,
  usePutBaseline,
  type PlotBaselineInput,
  type PlotBaselineView,
} from '../api/baselineApi'
import {
  PRACTICE_LABELS,
  practiceLabel,
  type IrrigationPractice,
} from '../api/baselineApi'
import { BaselineSurvey } from '../components/BaselineSurvey'

const YIELD_FORMAT = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })
const COST_FORMAT = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })

/** The practices the closed vocabulary holds, for the narrowing guard below. */
const KNOWN_PRACTICES = new Set<string>(Object.keys(PRACTICE_LABELS))

/** A `YYYY-MM-DD` from the wire, printed the way a Colombian reads a date. */
function enrolledOnLabel(value: string): string {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

/**
 * docs/07:150 lets `owner` and `technician` fill the survey; the server answers
 * `403` to any other role (docs/04-api.md:233). This gate only decides whether
 * to offer the form at all — the server still owns the answer.
 */
function canFill(role: Role | null): boolean {
  return role === 'owner' || role === 'technician'
}

/** The 422 the server sends for a `crop_id` outside the catalog names the field
 * in English; the producer needs the field's own name instead. */
function describeBaselineError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return 'Tu rol no puede cambiar esta encuesta.'
    if (err.status === 422) return err.detail ?? err.title
  }
  return describeApiError(err)
}

function SurveySummary({ baseline, cropName }: { baseline: PlotBaselineView; cropName: string }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-base">
      <dt className="text-text-muted">Cultivo del último ciclo</dt>
      <dd>{cropName}</dd>
      <dt className="text-text-muted">Rendimiento</dt>
      <dd>{YIELD_FORMAT.format(baseline.last_yield_kg_ha)} kg/ha</dd>
      <dt className="text-text-muted">Costo aproximado</dt>
      {/* A figure the farmer never gave is missing evidence, not zero
          (docs/03-modelo-datos.md:426-430), so it never prints as one. */}
      <dd>
        {baseline.last_cost_cop_ha === null
          ? 'No lo registró'
          : `${COST_FORMAT.format(baseline.last_cost_cop_ha)} COP/ha`}
      </dd>
      <dt className="text-text-muted">Práctica de riego</dt>
      <dd>{practiceLabel(baseline.irrigation_practice)}</dd>
      <dt className="text-text-muted">Fecha de inscripción</dt>
      <dd>{enrolledOnLabel(baseline.enrolled_on)}</dd>
    </dl>
  )
}

export interface BaselineSectionProps {
  plotId: string
  /** Caller role in the active organization: only an owner or a technician fills the form. */
  callerRole?: Role | null
}

/**
 * The enrollment survey in the plot detail (docs/07:150, ADR-0024). Reads for
 * every member, fills only for `owner` and `technician`, and invites to
 * register while the plot has none — without a survey there is no figure to
 * measure the change against.
 */
export function BaselineSection({ plotId, callerRole = null }: BaselineSectionProps) {
  const orgId = useOrgId()
  const baselineQuery = usePlotBaseline(orgId, plotId)
  const putMutation = usePutBaseline(orgId, plotId)
  const cropsQuery = useCrops()
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mayFill = canFill(callerRole)
  const baseline = baselineQuery.data
  /** docs/04-api.md:233 answers `404` when the plot has no survey yet: an
   * invitation to register one, not a failure to report. */
  const missing = baselineQuery.isError && baselineQuery.error instanceof ApiError && baselineQuery.error.status === 404
  const cropName =
    cropsQuery.data?.find((crop) => crop.id === baseline?.crop_id)?.name_es ??
    (baseline ? `Cultivo ${baseline.crop_id}` : '')

  async function handleSubmit(input: PlotBaselineInput) {
    setError(null)
    try {
      await putMutation.mutateAsync(input)
      setEditing(false)
    } catch (err) {
      setError(describeBaselineError(err))
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Encuesta de inscripción</h2>
      {baseline && <SurveySummary baseline={baseline} cropName={cropName} />}
      {!baseline && baselineQuery.isPending && (
        <p className="text-base text-text-muted">Buscando la encuesta de esta parcela…</p>
      )}
      {!baseline && missing && (
        <p className="text-base text-text-muted">
          {mayFill
            ? 'Sin encuesta todavía se puede sembrar, pero no se puede medir el cambio de esta parcela. Registra cómo rindió el último ciclo para poder compararlo.'
            : 'Esta parcela todavía no tiene encuesta. Solo el dueño o un técnico pueden registrar la encuesta.'}
        </p>
      )}
      {!baseline && baselineQuery.isError && !missing && (
        <p className="text-base text-severity-critical">{describeBaselineError(baselineQuery.error)}</p>
      )}
      {mayFill && (baseline || missing) && !editing && (
        <div>
          <Button type="button" variant="ghost" onClick={() => setEditing(true)}>
            {baseline ? 'Editar encuesta' : 'Registrar encuesta'}
          </Button>
        </div>
      )}
      {!mayFill && (
        <p className="text-base text-text-muted">
          Tu rol no puede cambiar esta encuesta.
        </p>
      )}
      {editing && (
        <BaselineSurvey
          open
          onOpenChange={(open) => !open && setEditing(false)}
          crops={(cropsQuery.data ?? []).map((crop) => ({ id: crop.id, nameEs: crop.name_es }))}
          initial={
            baseline
              ? {
                  enrolled_on: baseline.enrolled_on,
                  crop_id: baseline.crop_id,
                  last_yield_kg_ha: baseline.last_yield_kg_ha,
                  last_cost_cop_ha: baseline.last_cost_cop_ha,
                  // The view types the practice as a plain string, so it is
                  // narrowed here against the same closed vocabulary the write
                  // demands; an unknown value becomes `none` (rainfed) rather
                  // than being sent back and rejected by the server's enum.
                  irrigation_practice: KNOWN_PRACTICES.has(
                    baseline.irrigation_practice as IrrigationPractice,
                  )
                    ? (baseline.irrigation_practice as IrrigationPractice)
                    : 'none',
                }
              : undefined
          }
          onSubmit={handleSubmit}
          saving={putMutation.isPending}
          error={error}
        />
      )}
    </section>
  )
}