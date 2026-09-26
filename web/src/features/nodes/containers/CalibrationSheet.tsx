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
import { ApiError } from '../../../lib/api/client'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useCreateCalibration, type CalibrationView, type SensorView } from '../api/nodesApi'
import {
  KIND_OPTIONS,
  METHOD_OPTIONS,
  type CalibrationKind,
  type CalibrationMethod,
} from '../components/calibrationLabels'

/** Param names per method, read from docs/03-modelo-datos.md:463-467 and the server's
 * `apply_calibration`, which is what validates them (422 with the detail it names). */
const PARAM_FIELDS: Record<CalibrationMethod, { name: string; label: string }[]> = {
  linear: [
    { name: 'scale', label: 'Escala (scale)' },
    { name: 'offset', label: 'Offset (offset)' },
  ],
  two_point: [
    { name: 'raw_dry', label: 'ADC en seco (raw_dry)' },
    { name: 'raw_wet', label: 'ADC en húmedo (raw_wet)' },
    { name: 'vwc_dry', label: 'VWC en seco (vwc_dry)' },
    { name: 'vwc_wet', label: 'VWC en húmedo (vwc_wet)' },
  ],
  polynomial: [{ name: 'coeffs', label: 'Coeficientes (c0, c1, c2), separados por comas' }],
}

/** The `params` object this method needs, or null while the form is incomplete. */
function buildParams(
  method: CalibrationMethod,
  values: Record<string, string>,
): Record<string, unknown> | null {
  if (method === 'polynomial') {
    const coeffs = (values.coeffs ?? '')
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
      .map(Number)
    return coeffs.length > 0 && coeffs.every(Number.isFinite) ? { coeffs } : null
  }
  const params: Record<string, number> = {}
  for (const field of PARAM_FIELDS[method]) {
    const raw = values[field.name] ?? ''
    const value = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(value)) return null
    params[field.name] = value
  }
  return params
}

/** A `datetime-local` value in the technician's own timezone, which is what that input
 * means; the wire keeps UTC. */
function toLocalInputValue(date: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

function describeCalibrationError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return err.detail ?? err.title
    if (err.status === 403) return 'Tu rol no puede calibrar este sensor.'
  }
  return describeApiError(err)
}

export interface CalibrationSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  nodeId: string
  sensor: Pick<SensorView, 'id' | 'channel_key'>
  onCreated: (calibration: CalibrationView) => void
}

/**
 * docs/03-modelo-datos.md:461-467: calibration is versioned and never edited in place, so
 * this form only ever creates the next version (`POST /sensors/{id}/calibrations`).
 */
export function CalibrationSheet({
  open,
  onOpenChange,
  nodeId,
  sensor,
  onCreated,
}: CalibrationSheetProps) {
  const [method, setMethod] = useState<CalibrationMethod>('linear')
  const [kind, setKind] = useState<CalibrationKind>('lab')
  const [values, setValues] = useState<Record<string, string>>({})
  const [rmse, setRmse] = useState('')
  const [validFrom, setValidFrom] = useState(() => toLocalInputValue(new Date()))
  const [error, setError] = useState<string | null>(null)
  const create = useCreateCalibration(nodeId)

  const params = buildParams(method, values)

  async function handleSubmit() {
    if (!params || validFrom === '') return
    setError(null)
    try {
      const created = await create.mutateAsync({
        sensorId: sensor.id,
        calibration: {
          method,
          kind,
          params,
          rmse_pct: rmse.trim() === '' ? null : Number(rmse),
          valid_from: new Date(validFrom).toISOString(),
        },
      })
      onCreated(created)
      onOpenChange(false)
    } catch (err) {
      setError(describeCalibrationError(err))
    }
  }

  return (
    <FormSheet
      open={open}
      onOpenChange={onOpenChange}
      title={`Calibrar ${sensor.channel_key}`}
      description="Crea una versión nueva de la calibración; la anterior no se edita."
      submitLabel="Guardar calibración"
      onSubmit={handleSubmit}
      submitDisabled={params === null || validFrom === ''}
      submitLoading={create.isPending}
    >
      <label className="flex flex-col gap-2 text-base" htmlFor="calibration-method">
        Método
        <Select value={method} onValueChange={(value) => setMethod(value as CalibrationMethod)}>
          <SelectTrigger id="calibration-method">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {METHOD_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="calibration-kind">
        Tipo
        <Select value={kind} onValueChange={(value) => setKind(value as CalibrationKind)}>
          <SelectTrigger id="calibration-kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KIND_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      {PARAM_FIELDS[method].map((field) => (
        <label
          key={field.name}
          className="flex flex-col gap-2 text-base"
          htmlFor={`calibration-${field.name}`}
        >
          {field.label}
          <Input
            id={`calibration-${field.name}`}
            value={values[field.name] ?? ''}
            onChange={(event) =>
              setValues((prev) => ({ ...prev, [field.name]: event.target.value }))
            }
          />
        </label>
      ))}

      <label className="flex flex-col gap-2 text-base" htmlFor="calibration-rmse">
        Error RMSE (%), opcional
        <Input
          id="calibration-rmse"
          type="number"
          step="any"
          value={rmse}
          onChange={(event) => setRmse(event.target.value)}
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="calibration-valid-from">
        Vigente desde
        <Input
          id="calibration-valid-from"
          type="datetime-local"
          value={validFrom}
          onChange={(event) => setValidFrom(event.target.value)}
        />
      </label>

      {error && <p className="text-base text-severity-critical">{error}</p>}
    </FormSheet>
  )
}
