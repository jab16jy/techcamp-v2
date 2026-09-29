import { useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { PhotoField } from '../../../design-system/patterns/PhotoField'
import { toast } from '../../../design-system/ui/toast'
import { useOrgId } from '../../../lib/api/session'
import { todayInBogota } from '../../../lib/date'
import { uuidv7 } from '../../../lib/db/ids'
import { saveLogbookEntry, type LogbookEntryDraft } from '../../../lib/db/local'
import type { LogbookEntryRow, LogbookKind } from '../../../lib/db/db'
import { usePhotoAttachments } from '../../../lib/photos/usePhotoAttachments'
import { LogbookEntryForm, type AlertOption, type PlotOption } from '../components/LogbookEntryForm'

export interface NewEntrySheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  orgId?: string | null
  plots?: PlotOption[]
  alerts?: AlertOption[]
  entryToEdit?: LogbookEntryRow | null
  onClose?: () => void
}

interface NewEntrySheetModalProps {
  open: boolean
  onClose: (next: boolean) => void
  activeOrgId: string | null
  plots: PlotOption[]
  alerts: AlertOption[]
  entryToEdit: LogbookEntryRow | null
}

function NewEntrySheetModal({
  open,
  onClose,
  activeOrgId,
  plots,
  alerts,
  entryToEdit,
}: NewEntrySheetModalProps) {
  const [plotId, setPlotId] = useState(() => entryToEdit?.plot_id ?? plots[0]?.id ?? '')
  const [kind, setKind] = useState<LogbookKind>(() => entryToEdit?.kind ?? 'harvest')
  const [occurredOn, setOccurredOn] = useState(() => entryToEdit?.occurred_on ?? todayInBogota())
  const [yieldKg, setYieldKg] = useState(() =>
    entryToEdit?.yield_kg !== null && entryToEdit?.yield_kg !== undefined
      ? String(entryToEdit.yield_kg)
      : '',
  )
  const [soldKg, setSoldKg] = useState(() =>
    entryToEdit?.sold_kg !== null && entryToEdit?.sold_kg !== undefined
      ? String(entryToEdit.sold_kg)
      : '',
  )
  const [salePriceCopPerKg, setSalePriceCopPerKg] = useState(() =>
    entryToEdit?.sale_price_cop_per_kg !== null && entryToEdit?.sale_price_cop_per_kg !== undefined
      ? String(entryToEdit.sale_price_cop_per_kg)
      : '',
  )
  const [irrigationMm, setIrrigationMm] = useState(() =>
    entryToEdit?.irrigation_mm !== null && entryToEdit?.irrigation_mm !== undefined
      ? String(entryToEdit.irrigation_mm)
      : '',
  )
  const [laborDays, setLaborDays] = useState(() =>
    entryToEdit?.labor_days !== null && entryToEdit?.labor_days !== undefined
      ? String(entryToEdit.labor_days)
      : '',
  )
  const [costCop, setCostCop] = useState(() =>
    entryToEdit?.cost_cop !== null && entryToEdit?.cost_cop !== undefined
      ? String(entryToEdit.cost_cop)
      : '',
  )
  const [quantity, setQuantity] = useState(() =>
    entryToEdit?.quantity !== null && entryToEdit?.quantity !== undefined
      ? String(entryToEdit.quantity)
      : '',
  )
  const [unit, setUnit] = useState(() => entryToEdit?.unit ?? '')
  const [notes, setNotes] = useState(() => entryToEdit?.notes ?? '')
  const [alertId, setAlertId] = useState<string | null>(() => entryToEdit?.alert_id ?? null)

  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Photos of the entry (ADR-0018). A brand new entry has no id yet, so its
  // photos are staged until the save; an entry being corrected already has one,
  // and its stored photos are listed next to the new ones.
  const photos = usePhotoAttachments('logbook_entry', entryToEdit?.id ?? null)

  const effectivePlotId = plotId || (plots[0]?.id ?? '')

  function validate(): boolean {
    const nextErrors: Record<string, string | null> = {}

    if (!effectivePlotId) {
      nextErrors.plotId = 'Selecciona una parcela.'
    }
    if (!occurredOn) {
      nextErrors.occurredOn = 'La fecha es requerida.'
    }

    if (kind === 'harvest') {
      const numYield = Number(yieldKg)
      if (!yieldKg.trim() || Number.isNaN(numYield) || numYield < 0) {
        nextErrors.yieldKg = 'El rendimiento es requerido y no puede ser negativo.'
      }

      const hasSold = soldKg.trim().length > 0
      const hasPrice = salePriceCopPerKg.trim().length > 0

      if (hasSold) {
        const numSold = Number(soldKg)
        if (Number.isNaN(numSold) || numSold < 0) {
          nextErrors.soldKg = 'La cantidad vendida debe ser mayor o igual a 0.'
        } else if (!Number.isNaN(numYield) && numSold > numYield) {
          nextErrors.soldKg = 'La cantidad vendida no puede superar el rendimiento.'
        }
        if (!hasPrice) {
          nextErrors.salePriceCopPerKg = 'Debes indicar el precio de venta si registras cantidad vendida.'
        }
      }
      if (hasPrice) {
        const numPrice = Number(salePriceCopPerKg)
        if (Number.isNaN(numPrice) || numPrice < 0) {
          nextErrors.salePriceCopPerKg = 'El precio debe ser mayor o igual a 0.'
        }
        if (!hasSold) {
          nextErrors.soldKg = 'Debes indicar la cantidad vendida si registras precio de venta.'
        }
      }
    } else if (kind === 'irrigation') {
      const numIrrigation = Number(irrigationMm)
      if (!irrigationMm.trim() || Number.isNaN(numIrrigation) || numIrrigation < 0) {
        nextErrors.irrigationMm = 'La lámina de riego debe ser mayor o igual a 0.'
      }
    } else if (kind === 'task') {
      const numLabor = Number(laborDays)
      if (!laborDays.trim() || Number.isNaN(numLabor) || numLabor < 0) {
        nextErrors.laborDays = 'Los jornales deben ser mayores o iguales a 0.'
      }
    } else if (kind === 'input' || kind === 'cost') {
      const numCost = Number(costCop)
      if (!costCop.trim() || Number.isNaN(numCost) || numCost < 0) {
        nextErrors.costCop = 'El costo es requerido y debe ser mayor o igual a 0.'
      }
    } else if (kind === 'observation') {
      // docs/03: observation no exige campos. Con alert_id usa quantity, unit y cost_cop como pérdidas.
      if (alertId) {
        if (costCop.trim()) {
          const numCost = Number(costCop)
          if (Number.isNaN(numCost) || numCost < 0) {
            nextErrors.costCop = 'El costo de pérdidas debe ser mayor o igual a 0.'
          }
        }
        if (quantity.trim()) {
          const numQty = Number(quantity)
          if (Number.isNaN(numQty) || numQty < 0) {
            nextErrors.quantity = 'La cantidad perdida debe ser mayor o igual a 0.'
          }
        }
      }
    }

    setErrors(nextErrors)
    return Object.keys(nextErrors).length === 0
  }

  async function handleSubmit() {
    setFormError(null)

    if (!validate()) {
      return
    }

    if (!activeOrgId) {
      setFormError('No se encontró la organización activa.')
      return
    }

    const selectedPlot = plots.find((p) => p.id === effectivePlotId)
    const isObsWithAlert = kind === 'observation' && alertId !== null

    setSaving(true)
    try {
      const draft: LogbookEntryDraft = {
        id: entryToEdit ? entryToEdit.id : uuidv7(),
        org_id: activeOrgId,
        plot_id: effectivePlotId,
        crop_cycle_id: selectedPlot?.activeCropCycleId ?? entryToEdit?.crop_cycle_id ?? null,
        kind,
        occurred_on: occurredOn,
        quantity: (kind === 'input' || isObsWithAlert) && quantity.trim() ? Number(quantity) : null,
        unit: (kind === 'input' || isObsWithAlert) && unit.trim() ? unit.trim() : null,
        cost_cop:
          (kind === 'input' || kind === 'cost' || isObsWithAlert) && costCop.trim()
            ? Number(costCop)
            : null,
        yield_kg: kind === 'harvest' && yieldKg.trim() ? Number(yieldKg) : null,
        sold_kg: kind === 'harvest' && soldKg.trim() ? Number(soldKg) : null,
        sale_price_cop_per_kg:
          kind === 'harvest' && salePriceCopPerKg.trim() ? Number(salePriceCopPerKg) : null,
        labor_days: kind === 'task' && laborDays.trim() ? Number(laborDays) : null,
        irrigation_mm: kind === 'irrigation' && irrigationMm.trim() ? Number(irrigationMm) : null,
        alert_id: alertId,
        notes: notes.trim() || null,
        created_by: entryToEdit?.created_by ?? null,
      }

      const entry = await saveLogbookEntry(draft)
      // The photos go with the entry they document; if this fails the catch
      // below shows it and the sheet stays open, so nothing is lost silently.
      await photos.attachTo(entry.id)
      toast('Guardado en el teléfono')
      onClose(false)
    } catch (err) {
      console.error('Error al guardar registro en bitácora:', err)
      setFormError('No se pudo guardar el registro. Intenta de nuevo.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <FormSheet
      open={open}
      onOpenChange={onClose}
      title={entryToEdit ? 'Corregir entrada de bitácora' : 'Nueva entrada de bitácora'}
      description="Registra labores, cosechas, riegos o costos en campo (funciona sin conexión)."
      submitLabel="Guardar"
      onSubmit={handleSubmit}
      submitLoading={saving}
      submitDisabled={photos.processing}
    >
      <LogbookEntryForm
        plots={plots}
        plotId={effectivePlotId}
        onPlotIdChange={(val) => {
          setPlotId(val)
          setErrors((prev) => ({ ...prev, plotId: null }))
        }}
        occurredOn={occurredOn}
        onOccurredOnChange={(val) => {
          setOccurredOn(val)
          setErrors((prev) => ({ ...prev, occurredOn: null }))
        }}
        kind={kind}
        onKindChange={(val) => {
          setKind(val)
          setErrors({})
        }}
        yieldKg={yieldKg}
        onYieldKgChange={(val) => {
          setYieldKg(val)
          setErrors((prev) => ({ ...prev, yieldKg: null, soldKg: null }))
        }}
        soldKg={soldKg}
        onSoldKgChange={(val) => {
          setSoldKg(val)
          setErrors((prev) => ({ ...prev, soldKg: null, salePriceCopPerKg: null }))
        }}
        salePriceCopPerKg={salePriceCopPerKg}
        onSalePriceCopPerKgChange={(val) => {
          setSalePriceCopPerKg(val)
          setErrors((prev) => ({ ...prev, soldKg: null, salePriceCopPerKg: null }))
        }}
        irrigationMm={irrigationMm}
        onIrrigationMmChange={(val) => {
          setIrrigationMm(val)
          setErrors((prev) => ({ ...prev, irrigationMm: null }))
        }}
        laborDays={laborDays}
        onLaborDaysChange={(val) => {
          setLaborDays(val)
          setErrors((prev) => ({ ...prev, laborDays: null }))
        }}
        costCop={costCop}
        onCostCopChange={(val) => {
          setCostCop(val)
          setErrors((prev) => ({ ...prev, costCop: null }))
        }}
        quantity={quantity}
        onQuantityChange={(val) => {
          setQuantity(val)
          setErrors((prev) => ({ ...prev, quantity: null }))
        }}
        unit={unit}
        onUnitChange={setUnit}
        notes={notes}
        onNotesChange={(val) => {
          setNotes(val)
          setErrors((prev) => ({ ...prev, notes: null }))
        }}
        alerts={alerts}
        alertId={alertId}
        onAlertIdChange={setAlertId}
        errors={errors}
        formError={formError}
      />
      <PhotoField
        items={photos.items}
        onPick={(files) => void photos.addFiles(files)}
        onRemove={(id) => void photos.remove(id)}
        error={photos.error}
        disabled={saving || photos.processing}
      />
    </FormSheet>
  )
}

export function NewEntrySheet({
  open,
  onOpenChange,
  orgId: propOrgId,
  plots = [],
  alerts = [],
  entryToEdit = null,
  onClose,
}: NewEntrySheetProps) {
  const contextOrgId = useOrgId()
  const activeOrgId = propOrgId ?? contextOrgId

  function handleClose(next: boolean) {
    if (!next) {
      onClose?.()
    }
    onOpenChange(next)
  }

  const formKey = `${open ? 'open' : 'closed'}-${entryToEdit?.id ?? 'new'}`

  return (
    <NewEntrySheetModal
      key={formKey}
      open={open}
      onClose={handleClose}
      activeOrgId={activeOrgId}
      plots={plots}
      alerts={alerts}
      entryToEdit={entryToEdit}
    />
  )
}
