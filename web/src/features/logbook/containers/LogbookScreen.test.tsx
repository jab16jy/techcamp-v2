import 'fake-indexeddb/auto'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { db } from '../../../lib/db/db'
import { resetLocalDb } from '../../../lib/db/testDb'
import { saveLogbookEntry, deleteLogbookEntry } from '../../../lib/db/local'
import { setSession, clearSession } from '../../../lib/api/session'
import { uuidv7 } from '../../../lib/db/ids'
import { LogbookScreen } from './LogbookScreen'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOT_ID = '018f0c2a-0000-7000-8000-0000000000bb'

function renderScreen(props: { onEditEntry?: (entry: unknown) => void } = {}) {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <LogbookScreen {...props} />
    </QueryClientProvider>,
  )
}

describe('LogbookScreen', () => {
  beforeEach(async () => {
    await resetLocalDb()
    setSession('token-123', ORG_ID)
  })

  afterEach(() => {
    clearSession()
    vi.restoreAllMocks()
  })

  it('shows a pending entry as Guardado en el teléfono, a rejected one with its reason and both actions, and never a deleted one', async () => {
    // 1. Pending entry
    await saveLogbookEntry({
      id: uuidv7(),
      org_id: ORG_ID,
      plot_id: PLOT_ID,
      crop_cycle_id: null,
      kind: 'harvest',
      occurred_on: '2026-09-28',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: 500,
      sold_kg: null,
      sale_price_cop_per_kg: null,
      labor_days: null,
      irrigation_mm: null,
      alert_id: null,
      notes: null,
      created_by: null,
    })

    // 2. Rejected entry
    const rejectedEntry = await saveLogbookEntry({
      id: uuidv7(),
      org_id: ORG_ID,
      plot_id: PLOT_ID,
      crop_cycle_id: null,
      kind: 'task',
      occurred_on: '2026-09-27',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: null,
      sold_kg: null,
      sale_price_cop_per_kg: null,
      labor_days: 2,
      irrigation_mm: null,
      alert_id: null,
      notes: null,
      created_by: null,
    })
    // Mark as rejected in Dexie
    await db.logbookEntries.update(rejectedEntry.id, {
      syncState: 'rejected',
      syncError: 'clock_skew',
    })

    // 3. Deleted entry
    const deletedEntry = await saveLogbookEntry({
      id: uuidv7(),
      org_id: ORG_ID,
      plot_id: PLOT_ID,
      crop_cycle_id: null,
      kind: 'irrigation',
      occurred_on: '2026-09-26',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: null,
      sold_kg: null,
      sale_price_cop_per_kg: null,
      labor_days: null,
      irrigation_mm: 20,
      alert_id: null,
      notes: null,
      created_by: null,
    })
    await deleteLogbookEntry(deletedEntry.id)

    renderScreen()

    // 1. Pending entry visible with "Guardado en el teléfono"
    expect(await screen.findByText('Guardado en el teléfono')).toBeInTheDocument()
    expect(screen.getByText('500 kg')).toBeInTheDocument()

    // 2. Rejected entry visible with plain Spanish reason and Corregir / Descartar
    expect(screen.getByText('Fecha u hora del dispositivo incorrecta')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Corregir' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Descartar' })).toBeInTheDocument()

    // 3. Negative assertion: deleted entry never shown
    expect(screen.queryByText('20 mm')).not.toBeInTheDocument()
  })

  it('deletes a rejected entry locally when Descartar is clicked', async () => {
    const rejectedEntry = await saveLogbookEntry({
      id: uuidv7(),
      org_id: ORG_ID,
      plot_id: PLOT_ID,
      crop_cycle_id: null,
      kind: 'task',
      occurred_on: '2026-09-27',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: null,
      sold_kg: null,
      sale_price_cop_per_kg: null,
      labor_days: 3,
      irrigation_mm: null,
      alert_id: null,
      notes: null,
      created_by: null,
    })
    await db.logbookEntries.update(rejectedEntry.id, {
      syncState: 'rejected',
      syncError: 'invalid',
    })

    renderScreen()

    expect(await screen.findByText('Datos no válidos')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }))

    await waitFor(async () => {
      const stored = await db.logbookEntries.get(rejectedEntry.id)
      expect(stored?.deleted_at).not.toBeNull()
    })
    expect(screen.queryByText('Datos no válidos')).not.toBeInTheDocument()
  })

  it('opens the sheet to correct a rejected entry when Corregir is clicked', async () => {
    const rejectedEntry = await saveLogbookEntry({
      id: uuidv7(),
      org_id: ORG_ID,
      plot_id: PLOT_ID,
      crop_cycle_id: null,
      kind: 'harvest',
      occurred_on: '2026-09-27',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: 50,
      sold_kg: 100,
      sale_price_cop_per_kg: 1500,
      labor_days: null,
      irrigation_mm: null,
      alert_id: null,
      notes: null,
      created_by: null,
    })
    await db.logbookEntries.update(rejectedEntry.id, {
      syncState: 'rejected',
      syncError: 'invalid',
    })

    renderScreen()

    expect(await screen.findByText('Datos no válidos')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Corregir' }))

    expect(await screen.findByText('Corregir entrada de bitácora')).toBeInTheDocument()
    expect(screen.getByDisplayValue('50')).toBeInTheDocument()
  })
})
