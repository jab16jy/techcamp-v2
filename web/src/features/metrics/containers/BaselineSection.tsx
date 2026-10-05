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
const DATE_FORMAT = new Intl.DateTimeFormat('es-CO')

/** The practices the closed vocabulary holds, for the narrowing guard below. */
const KNOWN_PRACTICES = new Set<string>(Object.keys(PRACTICE_LABELS))

/**
 * A `YYYY-MM-DD` from the wire, printed the way a Colombian reads a date.
 *
 * The parts go through `new Date(y, m - 1, d)` — a *local* date — because
 * `new Date('2026-01-15')` is UTC midnight, which in Bogotá is still the 14th
 * and would print a day early. Handing the parts to `Intl` keeps the locale's
 * own ordering and separators instead of hardcoding `dd/mm/yyyy`.
 */
function enrolledOnLabel(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  return DATE_FORMAT.format(new Date(year, month - 1, day))
}

/**
 * docs/07:150 lets `owner` and `technician` fill the survey; the server answers
 * `403` to any other role (docs/04-api.md:233). This gate only decides whether
 * to offer the form at all — the server still owns the answer.
 */
function canFill(role: Role | null): boolean {
  return role === 'owner' || role === 'technician'
}

/**
 * Field-specific copy for a `422`, keyed by the wire field the server named.
 *
 * The body cannot be shown raw: it names the Python field (`crop_id is not a
 * valid crop`), and an internal code is not something a producer can act on
 * (docs/07:14 plain Spanish). Only a field the body actually names earns a
 * message about that field: `client.ts` folds a Pydantic validation body into
 * its `title`/`detail` strings and drops `loc`, so a `422` that names no field
 * cannot be attributed and must not be guessed.
 */
const FIELD_422_MESSAGES: Record<string, string> = {
  crop_id: 'Ese cultivo no está en la lista. Elige otro de la lista de cultivos.',
}

/** Names no field on purpose: a refused figure the server did not attribute is
 * not this form's to explain, so the message names the save and the retry. */
const NEUTRAL_422_MESSAGE = 'No se pudo guardar la encuesta. Revisa los datos e intenta de nuevo.'

/**
 * Spanish copy for what the server refused, naming the problem and the way out.
 */
function describeBaselineError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return 'Tu rol no puede cambiar esta encuesta.'
    if (err.status === 422) {
      const body = `${err.title} ${err.detail ?? ''}`
      // Word-bounded so a field named inside another field's message cannot
      // borrow this one's copy.
      const field = Object.keys(FIELD_422_MESSAGES).find((name) =>
        new RegExp(`\\b${name}\\b`).test(body),
      )
      return field === undefined ? NEUTRAL_422_MESSAGE : FIELD_422_MESSAGES[field]
    }
  }
  return describeApiError(err)
}

/**
 * Why the crop select cannot be used yet, when the section needs it: the
 * catalog is the only source of the crop a required question demands, so an
 * empty or failed catalog would offer a form whose submit never enables. The
 * form is not rendered until the catalog answers.
 *
 * No retry of its own: `cropsQuery` is the sheet's shared catalog, and the
 * sheet already offers one recovery for that failure, so a second button here
 * would show the same problem and the same way out twice. This says what is
 * blocked; the recovery on the same screen is the shared one.
 */
function cropCatalogBlocker(
  cropCount: number,
  query: { isPending: boolean; isError: boolean },
  needed: boolean,
): { message: string; critical: boolean } | null {
  if (!needed || cropCount > 0) return null
  if (query.isError) {
    return {
      message: 'La lista de cultivos no cargó, así que la encuesta no se puede llenar todavía.',
      critical: true,
    }
  }
  if (query.isPending) return { message: 'Cargando la lista de cultivos…', critical: false }
  // A catalog that answered with no crops is a configuration a producer cannot
  // act on, so it reads as a failure rather than as an empty choice list.
  return {
    message: 'La lista de cultivos está vacía, así que la encuesta no se puede llenar.',
    critical: false,
  }
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
  const crops = cropsQuery.data ?? []
  const cropName =
    crops.find((crop) => crop.id === baseline?.crop_id)?.name_es ??
    (baseline ? `Cultivo ${baseline.crop_id}` : '')
  /** The crop select needs the catalog, so the invitation waits for it. */
  const catalogBlocker = cropCatalogBlocker(crops.length, cropsQuery, Boolean(baseline) || missing)

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
        // Nothing else on the page moves when a read fails, so without an
        // announcement a screen reader never learns the survey is unavailable
        // (same repair as the save error in `BaselineSurvey`).
        <p role="alert" className="text-base text-severity-critical">
          {describeBaselineError(baselineQuery.error)}
        </p>
      )}
      {catalogBlocker !== null && (
        <p
          role={catalogBlocker.critical ? 'alert' : undefined}
          className={
            catalogBlocker.critical ? 'text-base text-severity-critical' : 'text-base text-text-muted'
          }
        >
          {catalogBlocker.message}
        </p>
      )}
      {mayFill && catalogBlocker === null && (baseline || missing) && !editing && (
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
          crops={crops.map((crop) => ({ id: crop.id, nameEs: crop.name_es }))}
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