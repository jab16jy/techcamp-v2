import { lazy, Suspense, useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { Button } from '../../../design-system/ui/button'
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
import { useCreatePlot, type IrrigationSystem } from '../api/plotsApi'
import { buildPolygonGeoJson, MIN_POLYGON_VERTICES, type Vertex } from '../polygon'

// React.lazy, not a static import: keeps Leaflet (and this whole map chunk)
// out of the main bundle (docs/05-arquitectura.md:179, `npm run size` budget).
const PlotDrawMap = lazy(() => import('../components/PlotDrawMap'))

const IRRIGATION_OPTIONS: { value: IrrigationSystem; label: string }[] = [
  { value: 'none', label: 'Secano (sin riego)' },
  { value: 'drip', label: 'Goteo' },
  { value: 'sprinkler', label: 'Aspersión' },
  { value: 'gravity', label: 'Gravedad' },
]

export interface CreatePlotSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  farmId: string
}

/**
 * `POST /farms/{farm_id}/plots` (docs/04-api.md, ADR-0023): name, a drawn
 * polygon, irrigation system including rainfed, and efficiency/flow only
 * while irrigated. `area_ha` is never sent — the database derives it from
 * `boundary` (T1 decision).
 */
export function CreatePlotSheet({ open, onOpenChange, farmId }: CreatePlotSheetProps) {
  const [name, setName] = useState('')
  const [vertices, setVertices] = useState<Vertex[]>([])
  const [irrigationSystem, setIrrigationSystem] = useState<IrrigationSystem>('none')
  const [efficiency, setEfficiency] = useState('')
  const [flow, setFlow] = useState('')
  const [error, setError] = useState<string | null>(null)
  const mutation = useCreatePlot(farmId)

  const isIrrigated = irrigationSystem !== 'none'
  const canSubmit = name.trim().length > 0 && vertices.length >= MIN_POLYGON_VERTICES

  function reset() {
    setName('')
    setVertices([])
    setIrrigationSystem('none')
    setEfficiency('')
    setFlow('')
    setError(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  function handleIrrigationChange(value: IrrigationSystem) {
    setIrrigationSystem(value)
    if (value === 'none') {
      setEfficiency('')
      setFlow('')
    }
  }

  async function handleSubmit() {
    setError(null)
    try {
      await mutation.mutateAsync({
        name,
        boundary: buildPolygonGeoJson(vertices),
        irrigation_system: irrigationSystem,
        irrigation_efficiency: isIrrigated && efficiency ? Number(efficiency) : null,
        system_flow_lph: isIrrigated && flow ? Number(flow) : null,
      })
      reset()
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setError(err.detail ?? err.title)
      } else {
        setError(describeApiError(err))
      }
    }
  }

  return (
    <FormSheet
      open={open}
      onOpenChange={handleOpenChange}
      title="Nueva parcela"
      submitLabel="Crear parcela"
      onSubmit={handleSubmit}
      submitDisabled={!canSubmit}
      submitLoading={mutation.isPending}
    >
      <label className="flex flex-col gap-2 text-base" htmlFor="plot-name">
        Nombre
        <Input id="plot-name" value={name} onChange={(event) => setName(event.target.value)} />
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-base">Polígono de la parcela</span>
        <Suspense fallback={<div className="h-64 w-full rounded-md bg-surface-raised" />}>
          <PlotDrawMap
            vertices={vertices}
            onMapClick={(vertex) => setVertices((prev) => [...prev, vertex])}
          />
        </Suspense>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => setVertices((prev) => prev.slice(0, -1))}
            disabled={vertices.length === 0}
          >
            Deshacer
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => setVertices([])}
            disabled={vertices.length === 0}
          >
            Limpiar
          </Button>
        </div>
        <p className="text-sm text-text-muted">
          {vertices.length} punto{vertices.length === 1 ? '' : 's'}
          {vertices.length < MIN_POLYGON_VERTICES &&
            ` · toca el mapa para agregar al menos ${MIN_POLYGON_VERTICES}`}
        </p>
      </div>

      <label className="flex flex-col gap-2 text-base" htmlFor="plot-irrigation">
        Sistema de riego
        <Select
          value={irrigationSystem}
          onValueChange={(value) => handleIrrigationChange(value as IrrigationSystem)}
        >
          <SelectTrigger id="plot-irrigation">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {IRRIGATION_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      {isIrrigated && (
        <>
          <label className="flex flex-col gap-2 text-base" htmlFor="plot-efficiency">
            Eficiencia de riego (0–1)
            <Input
              id="plot-efficiency"
              type="number"
              step="any"
              min={0}
              max={1}
              value={efficiency}
              onChange={(event) => setEfficiency(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="plot-flow">
            Caudal (L/h)
            <Input
              id="plot-flow"
              type="number"
              step="any"
              min={0}
              value={flow}
              onChange={(event) => setFlow(event.target.value)}
            />
          </label>
        </>
      )}

      {error && <p className="text-base text-severity-critical">{error}</p>}
    </FormSheet>
  )
}
