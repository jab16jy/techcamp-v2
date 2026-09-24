import { useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { Input } from '../../../design-system/ui/input'
import { ApiError } from '../../../lib/api/client'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useOrgId } from '../../../lib/api/session'
import { useCreateFarm } from '../api/plotsApi'

export interface CreateFarmSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * `POST /farms` (docs/04-api.md): name, municipality_code and a point
 * location as plain fields — no municipality picker exists yet (T1's
 * doc gap: no `municipality` table), and a single point doesn't need the
 * heavier polygon-drawing map T8 built for plots. `technician_id` is
 * omitted: optional on the API, and out of this task's named scope.
 */
export function CreateFarmSheet({ open, onOpenChange }: CreateFarmSheetProps) {
  const orgId = useOrgId()
  const [name, setName] = useState('')
  const [municipalityCode, setMunicipalityCode] = useState('')
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [error, setError] = useState<string | null>(null)
  const mutation = useCreateFarm(orgId)

  const canSubmit =
    name.trim().length > 0 &&
    municipalityCode.trim().length > 0 &&
    latitude.trim().length > 0 &&
    longitude.trim().length > 0

  function reset() {
    setName('')
    setMunicipalityCode('')
    setLatitude('')
    setLongitude('')
    setError(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleSubmit() {
    if (!orgId) return
    setError(null)
    try {
      await mutation.mutateAsync({
        name,
        municipality_code: municipalityCode,
        location: { type: 'Point', coordinates: [Number(longitude), Number(latitude)] },
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
      title="Nueva finca"
      submitLabel="Crear finca"
      onSubmit={handleSubmit}
      submitDisabled={!canSubmit}
      submitLoading={mutation.isPending}
    >
      <label className="flex flex-col gap-2 text-base" htmlFor="farm-name">
        Nombre
        <Input id="farm-name" value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="flex flex-col gap-2 text-base" htmlFor="farm-municipality">
        Código de municipio (DIVIPOLA)
        <Input
          id="farm-municipality"
          value={municipalityCode}
          onChange={(event) => setMunicipalityCode(event.target.value)}
        />
      </label>
      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-2 text-base" htmlFor="farm-latitude">
          Latitud
          <Input
            id="farm-latitude"
            type="number"
            step="any"
            value={latitude}
            onChange={(event) => setLatitude(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-2 text-base" htmlFor="farm-longitude">
          Longitud
          <Input
            id="farm-longitude"
            type="number"
            step="any"
            value={longitude}
            onChange={(event) => setLongitude(event.target.value)}
          />
        </label>
      </div>
      {error && <p className="text-base text-severity-critical">{error}</p>}
    </FormSheet>
  )
}
