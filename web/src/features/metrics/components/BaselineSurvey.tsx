import { useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { Input } from '../../../design-system/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../../design-system/ui/select'
import {
  PRACTICE_LABELS,
  type IrrigationPractice,
  type PlotBaselineInput,
} from '../api/baselineApi'

/** The crop catalog as this form needs it: an id to send and the Spanish name to show. */
export interface SurveyCrop {
  id: number
  nameEs: string
}

/** The same four values, in the order a producer would say them: rainfed first. */
const PRACTICE_OPTIONS = (Object.keys(PRACTICE_LABELS) as IrrigationPractice[]).map((value) => ({
  value,
  label: PRACTICE_LABELS[value],
}))

/** A `YYYY-MM-DD` date for the `enrolled_on` the wire carries. */
function todayIso(): string {
  const now = new Date()
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function numberOrNull(raw: string): number | null {
  if (raw.trim() === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

export interface BaselineSurveyProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  crops: SurveyCrop[]
  /** The recorded survey, when there is one: the form edits it instead of starting blank. */
  initial?: PlotBaselineInput
  onSubmit: (input: PlotBaselineInput) => void
  saving: boolean
  error: string | null
}

/**
 * docs/07:150: a short form in the plot detail with the crop of the last cycle,
 * the yield, the approximate cost per hectare (optional) and the irrigation
 * practice. Plain Spanish with the unit in the label (docs/07:14), fixed choices
 * instead of free text, and every state carried by words as well as color
 * (docs/07:12).
 */
export function BaselineSurvey({
  open,
  onOpenChange,
  crops,
  initial,
  onSubmit,
  saving,
  error,
}: BaselineSurveyProps) {
  const [enrolledOn, setEnrolledOn] = useState(initial?.enrolled_on ?? todayIso())
  const [cropId, setCropId] = useState<string>(initial ? String(initial.crop_id) : '')
  const [yieldKgHa, setYieldKgHa] = useState(initial?.last_yield_kg_ha.toString() ?? '')
  const [costCopHa, setCostCopHa] = useState(initial?.last_cost_cop_ha?.toString() ?? '')
  const [practice, setPractice] = useState<string>(initial?.irrigation_practice ?? '')

  const yieldValue = numberOrNull(yieldKgHa)
  const complete = enrolledOn !== '' && cropId !== '' && practice !== '' && yieldValue !== null

  function handleSubmit() {
    if (!complete) return
    onSubmit({
      enrolled_on: enrolledOn,
      crop_id: Number(cropId),
      last_yield_kg_ha: yieldValue as number,
      // Left empty is a figure the farmer does not know, and that is missing
      // evidence rather than a cost of zero (docs/03-modelo-datos.md:426-430).
      last_cost_cop_ha: numberOrNull(costCopHa),
      irrigation_practice: practice as IrrigationPractice,
    })
  }

  return (
    <FormSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Encuesta de inscripción"
      description="Cómo rindió esta parcela antes de usar TechCamp. Con esto se mide el cambio después."
      submitLabel="Guardar encuesta"
      onSubmit={handleSubmit}
      submitDisabled={!complete}
      submitLoading={saving}
    >
      <label className="flex flex-col gap-2 text-base" htmlFor="baseline-enrolled-on">
        Fecha de inscripción
        <Input
          id="baseline-enrolled-on"
          type="date"
          value={enrolledOn}
          onChange={(event) => setEnrolledOn(event.target.value)}
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="baseline-crop">
        Cultivo del último ciclo
        <Select value={cropId} onValueChange={setCropId}>
          <SelectTrigger id="baseline-crop">
            <SelectValue placeholder="Elige el cultivo" />
          </SelectTrigger>
          <SelectContent>
            {crops.map((crop) => (
              <SelectItem key={crop.id} value={String(crop.id)}>
                {crop.nameEs}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="baseline-yield">
        Rendimiento del último ciclo (kg/ha)
        <Input
          id="baseline-yield"
          type="number"
          step="any"
          min={0}
          inputMode="decimal"
          value={yieldKgHa}
          onChange={(event) => setYieldKgHa(event.target.value)}
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="baseline-cost">
        Costo aproximado por hectárea (COP/ha), opcional
        <Input
          id="baseline-cost"
          type="number"
          step="any"
          min={0}
          inputMode="decimal"
          placeholder="No lo recuerdo"
          value={costCopHa}
          onChange={(event) => setCostCopHa(event.target.value)}
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="baseline-practice">
        ¿Cómo se regaba antes?
        <Select value={practice} onValueChange={setPractice}>
          <SelectTrigger id="baseline-practice">
            <SelectValue placeholder="Elige la forma de riego" />
          </SelectTrigger>
          <SelectContent>
            {PRACTICE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      {error && <p className="text-base text-severity-critical">{error}</p>}
    </FormSheet>
  )
}