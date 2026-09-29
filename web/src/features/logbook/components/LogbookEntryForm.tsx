import { Input } from '../../../design-system/ui/input'
import type { LogbookKind } from '../../../lib/db/db'

export interface PlotOption {
  id: string
  name: string
  activeCropCycleId?: string | null
}

export interface AlertOption {
  id: string
  plot_id: string
  title: string
}

export interface LogbookEntryFormProps {
  plots: PlotOption[]
  plotId: string
  onPlotIdChange: (id: string) => void
  occurredOn: string
  onOccurredOnChange: (val: string) => void
  kind: LogbookKind
  onKindChange: (kind: LogbookKind) => void
  yieldKg: string
  onYieldKgChange: (val: string) => void
  soldKg: string
  onSoldKgChange: (val: string) => void
  salePriceCopPerKg: string
  onSalePriceCopPerKgChange: (val: string) => void
  irrigationMm: string
  onIrrigationMmChange: (val: string) => void
  laborDays: string
  onLaborDaysChange: (val: string) => void
  costCop: string
  onCostCopChange: (val: string) => void
  quantity: string
  onQuantityChange: (val: string) => void
  unit: string
  onUnitChange: (val: string) => void
  notes: string
  onNotesChange: (val: string) => void
  alerts?: AlertOption[]
  alertId: string | null
  onAlertIdChange: (id: string | null) => void
  errors: Record<string, string | null>
  formError?: string | null
}

const KINDS: { value: LogbookKind; label: string }[] = [
  { value: 'harvest', label: 'Cosecha' },
  { value: 'irrigation', label: 'Riego' },
  { value: 'task', label: 'Labor' },
  { value: 'input', label: 'Insumo' },
  { value: 'cost', label: 'Costo' },
  { value: 'observation', label: 'Observación' },
]

export function LogbookEntryForm({
  plots,
  plotId,
  onPlotIdChange,
  occurredOn,
  onOccurredOnChange,
  kind,
  onKindChange,
  yieldKg,
  onYieldKgChange,
  soldKg,
  onSoldKgChange,
  salePriceCopPerKg,
  onSalePriceCopPerKgChange,
  irrigationMm,
  onIrrigationMmChange,
  laborDays,
  onLaborDaysChange,
  costCop,
  onCostCopChange,
  quantity,
  onQuantityChange,
  unit,
  onUnitChange,
  notes,
  onNotesChange,
  alerts = [],
  alertId,
  onAlertIdChange,
  errors,
  formError,
}: LogbookEntryFormProps) {
  const plotAlerts = alerts.filter((a) => a.plot_id === plotId)

  return (
    <div className="flex flex-col gap-4 text-text">
      {formError && (
        <p role="alert" className="text-base text-severity-critical">
          {formError}
        </p>
      )}

      {/* Plot Select */}
      <label className="flex flex-col gap-2 text-base" htmlFor="entry-plot">
        Parcela
        <select
          id="entry-plot"
          className="flex h-12 w-full min-w-0 rounded-md border border-text/20 bg-surface-raised px-3 text-base text-text outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          value={plotId}
          onChange={(e) => onPlotIdChange(e.target.value)}
          required
        >
          <option value="">Selecciona una parcela...</option>
          {plots.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {errors.plotId && <p role="alert" className="text-sm text-severity-critical">{errors.plotId}</p>}
      </label>

      {/* Kind Select */}
      <label className="flex flex-col gap-2 text-base" htmlFor="entry-kind">
        Tipo de registro
        <select
          id="entry-kind"
          className="flex h-12 w-full min-w-0 rounded-md border border-text/20 bg-surface-raised px-3 text-base text-text outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          value={kind}
          onChange={(e) => onKindChange(e.target.value as LogbookKind)}
          required
        >
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>

      {/* Date */}
      <label className="flex flex-col gap-2 text-base" htmlFor="entry-date">
        Fecha
        <Input
          id="entry-date"
          type="date"
          value={occurredOn}
          onChange={(e) => onOccurredOnChange(e.target.value)}
          required
        />
        {errors.occurredOn && <p role="alert" className="text-sm text-severity-critical">{errors.occurredOn}</p>}
      </label>

      {/* Kind-specific fields */}
      {kind === 'harvest' && (
        <>
          <label className="flex flex-col gap-2 text-base" htmlFor="entry-yield">
            Rendimiento (kg)
            <Input
              id="entry-yield"
              type="number"
              step="any"
              min="0.01"
              value={yieldKg}
              onChange={(e) => onYieldKgChange(e.target.value)}
              placeholder="0.00"
              required
            />
            {errors.yieldKg && <p role="alert" className="text-sm text-severity-critical">{errors.yieldKg}</p>}
          </label>

          <label className="flex flex-col gap-2 text-base" htmlFor="entry-sold">
            Cantidad vendida (kg)
            <Input
              id="entry-sold"
              type="number"
              step="any"
              min="0"
              value={soldKg}
              onChange={(e) => onSoldKgChange(e.target.value)}
              placeholder="0.00"
            />
            {errors.soldKg && <p role="alert" className="text-sm text-severity-critical">{errors.soldKg}</p>}
          </label>

          <label className="flex flex-col gap-2 text-base" htmlFor="entry-sale-price">
            Precio de venta (COP/kg)
            <Input
              id="entry-sale-price"
              type="number"
              step="any"
              min="0"
              value={salePriceCopPerKg}
              onChange={(e) => onSalePriceCopPerKgChange(e.target.value)}
              placeholder="0.00"
            />
            {errors.salePriceCopPerKg && (
              <p role="alert" className="text-sm text-severity-critical">{errors.salePriceCopPerKg}</p>
            )}
          </label>
        </>
      )}

      {kind === 'irrigation' && (
        <label className="flex flex-col gap-2 text-base" htmlFor="entry-irrigation">
          Lámina de riego (mm)
          <Input
            id="entry-irrigation"
            type="number"
            step="any"
            min="0.01"
            value={irrigationMm}
            onChange={(e) => onIrrigationMmChange(e.target.value)}
            placeholder="0.00"
            required
          />
          {errors.irrigationMm && <p role="alert" className="text-sm text-severity-critical">{errors.irrigationMm}</p>}
        </label>
      )}

      {kind === 'task' && (
        <label className="flex flex-col gap-2 text-base" htmlFor="entry-labor">
          Jornales
          <Input
            id="entry-labor"
            type="number"
            step="any"
            min="0.01"
            value={laborDays}
            onChange={(e) => onLaborDaysChange(e.target.value)}
            placeholder="0.00"
            required
          />
          {errors.laborDays && <p role="alert" className="text-sm text-severity-critical">{errors.laborDays}</p>}
        </label>
      )}

      {kind === 'input' && (
        <>
          <label className="flex flex-col gap-2 text-base" htmlFor="entry-cost">
            Costo (COP)
            <Input
              id="entry-cost"
              type="number"
              step="any"
              min="0"
              value={costCop}
              onChange={(e) => onCostCopChange(e.target.value)}
              placeholder="0"
              required
            />
            {errors.costCop && <p role="alert" className="text-sm text-severity-critical">{errors.costCop}</p>}
          </label>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-2 text-base" htmlFor="entry-quantity">
              Cantidad (opcional)
              <Input
                id="entry-quantity"
                type="number"
                step="any"
                min="0"
                value={quantity}
                onChange={(e) => onQuantityChange(e.target.value)}
                placeholder="0.00"
              />
            </label>
            <label className="flex flex-col gap-2 text-base" htmlFor="entry-unit">
              Unidad (opcional)
              <Input
                id="entry-unit"
                type="text"
                value={unit}
                onChange={(e) => onUnitChange(e.target.value)}
                placeholder="kg, bultos, L..."
              />
            </label>
          </div>
        </>
      )}

      {kind === 'cost' && (
        <label className="flex flex-col gap-2 text-base" htmlFor="entry-cost">
          Costo (COP)
          <Input
            id="entry-cost"
            type="number"
            step="any"
            min="0"
            value={costCop}
            onChange={(e) => onCostCopChange(e.target.value)}
            placeholder="0"
            required
          />
          {errors.costCop && <p role="alert" className="text-sm text-severity-critical">{errors.costCop}</p>}
        </label>
      )}

      {/* Observation notes are required, others optional */}
      <label className="flex flex-col gap-2 text-base" htmlFor="entry-notes">
        {kind === 'observation' ? 'Observación' : 'Notas (opcional)'}
        <Input
          id="entry-notes"
          type="text"
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
          placeholder={kind === 'observation' ? 'Describe lo observado en campo...' : 'Detalles adicionales...'}
          required={kind === 'observation'}
        />
        {errors.notes && <p role="alert" className="text-sm text-severity-critical">{errors.notes}</p>}
      </label>

      {/* Linked alert (if any open alerts for this plot) */}
      {plotAlerts.length > 0 && (
        <label className="flex flex-col gap-2 text-base" htmlFor="entry-alert">
          Alerta relacionada (opcional)
          <select
            id="entry-alert"
            className="flex h-12 w-full min-w-0 rounded-md border border-text/20 bg-surface-raised px-3 text-base text-text outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            value={alertId ?? ''}
            onChange={(e) => onAlertIdChange(e.target.value ? e.target.value : null)}
          >
            <option value="">Sin vincular a alerta</option>
            {plotAlerts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}
