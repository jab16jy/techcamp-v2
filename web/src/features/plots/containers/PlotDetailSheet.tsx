import { useState } from 'react'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '../../../design-system/ui/sheet'
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
import {
  useActiveCycle,
  useAutofillSoil,
  useCreateCycle,
  useCrops,
  usePatchCycle,
  usePutSoil,
  useSoilProfile,
  type CropCycleView,
  type SoilProfilePutRequest,
} from '../api/plotsApi'

export interface PlotDetailSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  plotId: string
  plotName: string
}

// Keys of `techcamp.farms.domain.models.FAO56_TEXTURE_WATER_LIMITS` (server, T4): the only
// texture strings the FAO-56 Table 19 fallback recognizes (anything else silently leaves
// `source = null`, T4 decision). Fixed choices instead of free text avoid that silent miss
// for a Spanish-speaking user who would otherwise type "arcilla", not "clay" (ponytail: this
// is a label list, not the FAO-56 math itself, which stays server-side).
const TEXTURE_OPTIONS: { value: string; label: string }[] = [
  { value: 'sand', label: 'Arena' },
  { value: 'loamy_sand', label: 'Arena franca' },
  { value: 'sandy_loam', label: 'Franco arenoso' },
  { value: 'loam', label: 'Franco' },
  { value: 'silt_loam', label: 'Franco limoso' },
  { value: 'silt', label: 'Limo' },
  { value: 'silty_clay_loam', label: 'Franco arcillo limoso' },
  { value: 'silty_clay', label: 'Arcillo limoso' },
  { value: 'clay', label: 'Arcilla' },
]

const SOIL_SOURCE_LABELS: Record<string, string> = {
  soilgrids: 'SoilGrids',
  lab: 'Laboratorio',
  fao56_texture: 'Tabla FAO-56 (por textura)',
}

const KC_SOURCE_LABELS: Record<string, string> = {
  fao56: 'FAO-56 (directo)',
  approximate: 'FAO-56 (aproximado)',
  local: 'Validado localmente',
  none: 'Sin validar',
}

const STAGE_LABELS: Record<string, string> = {
  initial: 'Inicial',
  development: 'Desarrollo',
  mid: 'Media',
  late: 'Final',
}

const CYCLE_STATUS_LABELS: Record<CropCycleView['status'], string> = {
  active: 'Activo',
  harvested: 'Cosechado',
  lost: 'Perdido',
}

/** A number input's value, or `null` for an empty field (T4/T8's "empty string clears the
 * field" convention, matching `CreatePlotSheet`'s efficiency/flow handling). */
function numberOrNull(value: string): number | null {
  return value.trim() === '' ? null : Number(value)
}

/** Dedicated 502/503 copy only for SoilGrids autofill (T5). */
function describeAutofillError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 502 || err.status === 503) {
      return 'SoilGrids no está disponible en este momento. Intenta de nuevo más tarde.'
    }
    if (err.status === 422 || err.status === 409) {
      return err.detail ?? err.title
    }
  }
  return describeApiError(err)
}

/** Error copy for soil PUT, cycle POST, cycle PATCH (#21 round 14): 502/503 goes to describeApiError. */
function describeActionError(err: unknown): string {
  if (err instanceof ApiError && (err.status === 422 || err.status === 409)) {
    return err.detail ?? err.title
  }
  return describeApiError(err)
}

function SoilSection({ plotId }: { plotId: string }) {
  const soilQuery = useSoilProfile(plotId)
  const autofillMutation = useAutofillSoil(plotId)
  const putMutation = usePutSoil(plotId)
  const [editing, setEditing] = useState(false)
  const [texture, setTexture] = useState('')
  const [ph, setPh] = useState('')
  const [organicMatter, setOrganicMatter] = useState('')
  const [fieldCapacity, setFieldCapacity] = useState('')
  const [wiltingPoint, setWiltingPoint] = useState('')
  const [rootDepth, setRootDepth] = useState('')
  const [error, setError] = useState<string | null>(null)

  const profile = soilQuery.data

  function openEdit() {
    setTexture(profile?.texture ?? '')
    setPh(profile?.ph?.toString() ?? '')
    setOrganicMatter(profile?.organic_matter_pct?.toString() ?? '')
    setFieldCapacity(profile?.field_capacity_pct?.toString() ?? '')
    setWiltingPoint(profile?.wilting_point_pct?.toString() ?? '')
    setRootDepth(profile?.root_depth_cm?.toString() ?? '')
    setError(null)
    setEditing(true)
  }

  async function handleAutofill() {
    setError(null)
    try {
      await autofillMutation.mutateAsync()
    } catch (err) {
      setError(describeAutofillError(err))
    }
  }

  async function handleSaveManual() {
    setError(null)
    const payload: SoilProfilePutRequest = {
      texture: texture || null,
      ph: numberOrNull(ph),
      organic_matter_pct: numberOrNull(organicMatter),
      field_capacity_pct: numberOrNull(fieldCapacity),
      wilting_point_pct: numberOrNull(wiltingPoint),
      root_depth_cm: numberOrNull(rootDepth),
    }
    try {
      await putMutation.mutateAsync(payload)
      setEditing(false)
    } catch (err) {
      setError(describeActionError(err))
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Suelo</h2>
      {profile ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-base">
          <dt className="text-text-muted">Textura</dt>
          <dd>{TEXTURE_OPTIONS.find((option) => option.value === profile.texture)?.label ?? profile.texture ?? '—'}</dd>
          <dt className="text-text-muted">pH</dt>
          <dd>{profile.ph ?? '—'}</dd>
          <dt className="text-text-muted">Materia orgánica</dt>
          <dd>{profile.organic_matter_pct ?? '—'}%</dd>
          <dt className="text-text-muted">θFC (capacidad de campo)</dt>
          <dd>{profile.field_capacity_pct ?? '—'}%</dd>
          <dt className="text-text-muted">θWP (punto de marchitez)</dt>
          <dd>{profile.wilting_point_pct ?? '—'}%</dd>
          <dt className="text-text-muted">Profundidad radicular</dt>
          <dd>{profile.root_depth_cm ?? '—'} cm</dd>
          <dt className="text-text-muted">Fuente</dt>
          <dd>{profile.source ? (SOIL_SOURCE_LABELS[profile.source] ?? profile.source) : 'Sin determinar'}</dd>
        </dl>
      ) : (
        <p className="text-base text-text-muted">Todavía no hay datos de suelo en esta sesión.</p>
      )}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={handleAutofill}
          disabled={editing}
          loading={autofillMutation.isPending}
        >
          Autocompletar desde SoilGrids
        </Button>
        <Button type="button" variant="ghost" onClick={editing ? () => setEditing(false) : openEdit}>
          {editing ? 'Cancelar edición' : 'Editar manualmente'}
        </Button>
      </div>
      {editing && (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-texture">
            Textura
            <Select value={texture} onValueChange={setTexture}>
              <SelectTrigger id="soil-texture">
                <SelectValue placeholder="Sin especificar" />
              </SelectTrigger>
              <SelectContent>
                {TEXTURE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-ph">
            pH (0–14)
            <Input id="soil-ph" type="number" step="any" min={0} max={14} value={ph} onChange={(event) => setPh(event.target.value)} />
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-organic-matter">
            Materia orgánica (%)
            <Input
              id="soil-organic-matter"
              type="number"
              step="any"
              min={0}
              max={100}
              value={organicMatter}
              onChange={(event) => setOrganicMatter(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-field-capacity">
            θFC — capacidad de campo (%)
            <Input
              id="soil-field-capacity"
              type="number"
              step="any"
              min={0}
              max={100}
              value={fieldCapacity}
              onChange={(event) => setFieldCapacity(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-wilting-point">
            θWP — punto de marchitez (%)
            <Input
              id="soil-wilting-point"
              type="number"
              step="any"
              min={0}
              max={100}
              value={wiltingPoint}
              onChange={(event) => setWiltingPoint(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-2 text-base" htmlFor="soil-root-depth">
            Profundidad radicular (cm)
            <Input
              id="soil-root-depth"
              type="number"
              step="any"
              min={0}
              value={rootDepth}
              onChange={(event) => setRootDepth(event.target.value)}
            />
          </label>
          <Button type="button" variant="primary" onClick={handleSaveManual} loading={putMutation.isPending}>
            Guardar suelo
          </Button>
        </div>
      )}
      {error && <p className="text-base text-severity-critical">{error}</p>}
    </section>
  )
}

function CycleSection({ plotId }: { plotId: string }) {
  const cycleQuery = useActiveCycle(plotId)
  const cropsQuery = useCrops()
  const createMutation = useCreateCycle(plotId)
  const patchMutation = usePatchCycle(plotId)
  const [cropId, setCropId] = useState('')
  const [sownOn, setSownOn] = useState('')
  const [error, setError] = useState<string | null>(null)

  const cycle = cycleQuery.data?.status === 'active' ? cycleQuery.data : null
  const selectedCrop = cropsQuery.data?.find((crop) => String(crop.id) === cropId)
  const canSubmit = cropId !== '' && sownOn !== ''

  async function handleStartCycle() {
    setError(null)
    try {
      await createMutation.mutateAsync({ crop_id: Number(cropId), sown_on: sownOn })
      setCropId('')
      setSownOn('')
    } catch (err) {
      setError(describeActionError(err))
    }
  }

  async function handleSetStatus(status: 'harvested' | 'lost') {
    if (!cycle) return
    setError(null)
    try {
      await patchMutation.mutateAsync({ cycleId: cycle.id, changes: { status } })
    } catch (err) {
      setError(describeActionError(err))
    }
  }

  function cropName(cropIdValue: number): string {
    return cropsQuery.data?.find((crop) => crop.id === cropIdValue)?.name_es ?? `Cultivo #${cropIdValue}`
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Ciclo de cultivo</h2>
      {cycle ? (
        <div className="flex flex-col gap-2">
          <p className="text-base">
            {cropName(cycle.crop_id)} · sembrado el {cycle.sown_on}
            {cycle.expected_harvest_on && ` · cosecha esperada el ${cycle.expected_harvest_on}`}
          </p>
          <p className="text-sm text-text-muted">Estado: {CYCLE_STATUS_LABELS[cycle.status]}</p>
          {cycle.status === 'active' && (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => handleSetStatus('harvested')}
                loading={patchMutation.isPending}
              >
                Registrar cosecha
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => handleSetStatus('lost')}
                loading={patchMutation.isPending}
              >
                Registrar pérdida
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {cropsQuery.isLoading && (
            <p className="text-base text-text-muted">Cargando cultivos…</p>
          )}
          {cropsQuery.isError && (
            <div className="flex flex-col gap-2">
              <p className="text-base text-severity-critical">{describeApiError(cropsQuery.error)}</p>
              <div>
                <Button type="button" variant="secondary" onClick={() => cropsQuery.refetch()}>
                  Reintentar
                </Button>
              </div>
            </div>
          )}
          {cropsQuery.isSuccess && (
            <>
              <label className="flex flex-col gap-2 text-base" htmlFor="cycle-crop">
                Cultivo
                <Select value={cropId} onValueChange={setCropId}>
                  <SelectTrigger id="cycle-crop">
                    <SelectValue placeholder="Selecciona un cultivo" />
                  </SelectTrigger>
                  <SelectContent>
                    {cropsQuery.data.map((crop) => (
                      <SelectItem key={crop.id} value={String(crop.id)}>
                        {crop.name_es}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              {selectedCrop && (
                <p className="text-sm text-text-muted">
                  Kc ({KC_SOURCE_LABELS[selectedCrop.kc_source] ?? selectedCrop.kc_source}):{' '}
                  {selectedCrop.stages.length > 0
                    ? selectedCrop.stages
                        .map((stage) => `${STAGE_LABELS[stage.stage] ?? stage.stage} ${stage.kc}`)
                        .join(' · ')
                    : 'sin etapas registradas'}
                </p>
              )}
              <label className="flex flex-col gap-2 text-base" htmlFor="cycle-sown-on">
                Fecha de siembra
                <Input
                  id="cycle-sown-on"
                  type="date"
                  value={sownOn}
                  onChange={(event) => setSownOn(event.target.value)}
                />
              </label>
              <Button
                type="button"
                variant="primary"
                onClick={handleStartCycle}
                disabled={!canSubmit}
                loading={createMutation.isPending}
              >
                Iniciar ciclo
              </Button>
            </>
          )}
        </div>
      )}
      {error && <p className="text-base text-severity-critical">{error}</p>}
    </section>
  )
}

/**
 * Plot detail (docs/07 mapa de pantallas: `plots --> plotd[Parcela: polígono, suelo,
 * ciclo]`; the polygon itself is T8's creation map, not re-edited here — doc gap, no
 * screen layout is specified beyond that navigation node, T9). Opened from a plot row in
 * `PlotsList` (entry point, replacing an inert `<li>` with a button).
 */
export function PlotDetailSheet({ open, onOpenChange, plotId, plotName }: PlotDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{plotName}</SheetTitle>
          <SheetDescription>Suelo y ciclo de cultivo de esta parcela.</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-6">
          <SoilSection plotId={plotId} />
          <CycleSection plotId={plotId} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
