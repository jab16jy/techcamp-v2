import { liveQuery } from 'dexie'
import { useEffect, useState, type ReactNode } from 'react'
import { minutesSince } from '../../../design-system/components/format'
import { SyncIndicator } from '../../../design-system/components/SyncIndicator'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { Button } from '../../../design-system/ui/button'
import { NotebookIcon } from '../../../design-system/ui/icons'
import { toast } from '../../../design-system/ui/toast'
import { useActiveOrgRole, type Role } from '../../../lib/api/me'
import { useOrgId } from '../../../lib/api/session'
import { db, type LogbookEntryRow } from '../../../lib/db/db'
import { usePendingCount } from '../../../lib/db/live'
import { deleteLogbookEntry } from '../../../lib/db/local'
import { useOnlineStatus, useSyncState } from '../../../lib/sync/syncState'
import { LogbookItem } from '../components/LogbookItem'

export interface PlotInfo {
  id: string
  name: string
}

export interface LogbookScreenProps {
  plots?: PlotInfo[]
  onEditEntry?: (entry: LogbookEntryRow) => void
  renderNewEntrySheet?: (props: {
    open: boolean
    onOpenChange: (open: boolean) => void
    entryToEdit?: LogbookEntryRow | null
    onClose?: () => void
  }) => ReactNode
}

/**
 * Logbook screen (docs/07 §Mapa de pantallas, docs/06 §7).
 * Lists entries of the active organization from Dexie local store (live query), newest first.
 * Displays sync states: pending ("Guardado en el teléfono"), synced, conflict_overwritten, rejected.
 */
export function LogbookScreen({ plots = [], onEditEntry, renderNewEntrySheet }: LogbookScreenProps) {
  const orgId = useOrgId()
  const role: Role | null = useActiveOrgRole()
  const [entries, setEntries] = useState<LogbookEntryRow[]>([])
  const [sheetOpen, setSheetOpen] = useState(false)
  const [selectedEntry, setSelectedEntry] = useState<LogbookEntryRow | null>(null)

  const online = useOnlineStatus()
  const pendingCount = usePendingCount()
  const syncState = useSyncState()
  const lastDataMinutesAgo = minutesSince(syncState.lastSyncedAt)

  const canWrite = role !== 'viewer'

  useEffect(() => {
    if (!orgId) return

    const subscription = liveQuery(async () => {
      const items = await db.logbookEntries
        .filter((entry) => entry.org_id === orgId && entry.deleted_at === null)
        .toArray()

      return items.sort((a, b) => {
        const dateCmp = b.occurred_on.localeCompare(a.occurred_on)
        if (dateCmp !== 0) return dateCmp
        return b.client_updated_at.localeCompare(a.client_updated_at)
      })
    }).subscribe({
      next: (val) => setEntries(val),
    })

    return () => {
      subscription.unsubscribe()
    }
  }, [orgId])

  async function handleDiscard(id: string) {
    try {
      await deleteLogbookEntry(id)
      toast('Registro descartado')
    } catch (err) {
      console.error('Error al descartar registro:', err)
      toast('Error al descartar registro')
    }
  }

  function handleEdit(entry: LogbookEntryRow) {
    if (onEditEntry) {
      onEditEntry(entry)
      return
    }
    setSelectedEntry(entry)
    setSheetOpen(true)
  }

  function handleOpenNew() {
    setSelectedEntry(null)
    setSheetOpen(true)
  }

  const plotMap = new Map<string, string>(plots.map((p) => [p.id, p.name]))
  const displayEntries = orgId ? entries : []

  return (
    <div className="px-0 pt-6">
      {/* Header */}
      <header className="flex items-center justify-between gap-2 px-4">
        <div>
          <h1 className="font-serif text-2xl">Bitácora</h1>
          <SyncIndicator
            online={online}
            pendingCount={pendingCount}
            lastDataMinutesAgo={lastDataMinutesAgo}
            syncStopped={syncState.syncStopped}
            className="mt-1"
          />
        </div>
        {canWrite && orgId && (
          <Button
            type="button"
            className="min-h-12"
            onClick={handleOpenNew}
          >
            Nueva entrada
          </Button>
        )}
      </header>

      {/* Content */}
      <section className="mt-4" aria-label="Lista de registros">
        {!orgId ? (
          <EmptyState
            icon={<NotebookIcon className="size-10" />}
            title="Elige una organización"
            description="Vuelve a ingresar para elegir la organización de tu cuenta."
          />
        ) : displayEntries.length === 0 ? (
          <EmptyState
            icon={<NotebookIcon className="size-10" />}
            title="Sin registros en la bitácora"
            description="Registra las labores, cosechas, riegos y costos de tus parcelas."
            action={
              canWrite ? (
                <Button
                  type="button"
                  className="min-h-12"
                  onClick={handleOpenNew}
                >
                  Nueva entrada
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="mx-4 divide-y divide-text/10 rounded-lg bg-surface-raised">
            {displayEntries.map((entry) => (
              <li key={entry.id}>
                <LogbookItem
                  entry={entry}
                  plotName={plotMap.get(entry.plot_id)}
                  onEdit={handleEdit}
                  onDiscard={handleDiscard}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* New entry sheet renderer (if provided) */}
      {renderNewEntrySheet?.({
        open: sheetOpen,
        onOpenChange: setSheetOpen,
        entryToEdit: selectedEntry,
        onClose: () => {
          setSheetOpen(false)
          setSelectedEntry(null)
        },
      })}
    </div>
  )
}
