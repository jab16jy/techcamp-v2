import type { CalibrationCreateRequest } from '../api/nodesApi'

export type CalibrationMethod = CalibrationCreateRequest['method']
export type CalibrationKind = CalibrationCreateRequest['kind']

/** docs/04-api.md:86 and the server's `CalibrationMethod`/`CalibrationKind` enums. */
export const METHOD_OPTIONS: { value: CalibrationMethod; label: string }[] = [
  { value: 'linear', label: 'Lineal' },
  { value: 'two_point', label: 'Dos puntos' },
  { value: 'polynomial', label: 'Polinomio' },
]

export const KIND_OPTIONS: { value: CalibrationKind; label: string }[] = [
  { value: 'lab', label: 'Laboratorio' },
  { value: 'field', label: 'Campo' },
]

export const METHOD_LABELS: Record<CalibrationMethod, string> = {
  linear: 'Lineal',
  two_point: 'Dos puntos',
  polynomial: 'Polinomio',
}

export const KIND_LABELS: Record<CalibrationKind, string> = {
  lab: 'Laboratorio',
  field: 'Campo',
}
