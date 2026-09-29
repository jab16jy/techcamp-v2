import { Button } from '../../../design-system/ui/button'
import type { LogbookEntryRow } from '../../../lib/db/db'

export interface LogbookItemProps {
  entry: LogbookEntryRow
  plotName?: string
  onEdit?: (entry: LogbookEntryRow) => void
  onDiscard?: (id: string) => void
}

const KIND_LABELS: Record<string, string> = {
  harvest: 'Cosecha',
  irrigation: 'Riego',
  task: 'Labor',
  input: 'Insumo',
  cost: 'Costo',
  observation: 'Observación',
}

const REJECT_REASONS: Record<string, string> = {
  clock_skew: 'Fecha u hora del dispositivo incorrecta',
  invalid: 'Datos no válidos',
  not_found: 'No encontrado en el servidor',
  alert_plot_mismatch: 'La alerta no corresponde a esta parcela',
  forbidden: 'Sin permiso para registrar en esta organización',
}

function formatKeyAmount(entry: LogbookEntryRow): string | null {
  switch (entry.kind) {
    case 'harvest': {
      if (entry.yield_kg === null) return null
      let text = `${entry.yield_kg} kg`
      if (entry.sold_kg !== null) {
        text += ` · ${entry.sold_kg} kg vendidos`
      }
      return text
    }
    case 'irrigation':
      return entry.irrigation_mm !== null ? `${entry.irrigation_mm} mm` : null
    case 'task':
      return entry.labor_days !== null
        ? `${entry.labor_days} jornal${entry.labor_days === 1 ? '' : 'es'}`
        : null
    case 'input':
      if (entry.cost_cop !== null) return `$ ${entry.cost_cop.toLocaleString('es-CO')}`
      if (entry.quantity !== null) return `${entry.quantity} ${entry.unit ?? ''}`.trim()
      return null
    case 'cost':
      return entry.cost_cop !== null ? `$ ${entry.cost_cop.toLocaleString('es-CO')}` : null
    case 'observation':
      return entry.notes || null
    default:
      return null
  }
}

export function LogbookItem({ entry, plotName, onEdit, onDiscard }: LogbookItemProps) {
  const kindLabel = KIND_LABELS[entry.kind] ?? entry.kind
  const amount = formatKeyAmount(entry)
  const isRejected = entry.syncState === 'rejected'
  const isConflict = entry.syncState === 'conflict_overwritten'
  const isPending = entry.syncState === 'pending'
  const rejectReason = entry.syncError ? REJECT_REASONS[entry.syncError] ?? entry.syncError : 'Error al sincronizar'

  return (
    <article className="flex flex-col gap-2 p-4 text-text">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-semibold">{kindLabel}</h2>
          {plotName && <span className="text-sm text-text-muted">· {plotName}</span>}
        </div>
        <time dateTime={entry.occurred_on} className="text-sm text-text-muted">
          {entry.occurred_on}
        </time>
      </div>

      {amount && <p className="text-sm font-medium">{amount}</p>}

      {entry.notes && entry.kind !== 'observation' && (
        <p className="text-sm text-text-muted line-clamp-2">{entry.notes}</p>
      )}

      {/* Sync State line */}
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-sm">
        {isPending && (
          <span className="text-text-muted">Guardado en el teléfono</span>
        )}
        {entry.syncState === 'synced' && (
          <span className="text-text-muted">Sincronizado</span>
        )}
        {isConflict && (
          <span className="text-offline font-medium">
            Sobrescrito en el servidor (la edición más reciente del servidor prevaleció)
          </span>
        )}
        {isRejected && (
          <div className="flex w-full flex-col gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-status-stress font-medium">
              {rejectReason}
            </span>
            <div className="flex items-center gap-2">
              {onEdit && (
                <Button
                  type="button"
                  variant="secondary"
                  className="min-h-12 px-4"
                  onClick={() => onEdit(entry)}
                >
                  Corregir
                </Button>
              )}
              {onDiscard && (
                <Button
                  type="button"
                  variant="ghost"
                  className="min-h-12 px-4 text-status-stress hover:bg-status-stress/10"
                  onClick={() => onDiscard(entry.id)}
                >
                  Descartar
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </article>
  )
}
