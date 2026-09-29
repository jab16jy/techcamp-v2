import 'fake-indexeddb/auto'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { db } from '../../../lib/db/db'
import { resetLocalDb } from '../../../lib/db/testDb'
import { setSession, clearSession } from '../../../lib/api/session'
import { NewEntrySheet } from './NewEntrySheet'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const PLOTS = [
  { id: '018f0c2a-0000-7000-8000-0000000000bb', name: 'Parcela Norte' },
  { id: '018f0c2a-0000-7000-8000-0000000000cc', name: 'Parcela Sur' },
]

function renderSheet(props: Partial<React.ComponentProps<typeof NewEntrySheet>> = {}) {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <NewEntrySheet
        open={true}
        onOpenChange={() => {}}
        orgId={ORG_ID}
        plots={PLOTS}
        {...props}
      />
    </QueryClientProvider>,
  )
}

describe('NewEntrySheet', () => {
  beforeEach(async () => {
    await resetLocalDb()
    setSession('token-123', ORG_ID)
  })

  afterEach(() => {
    clearSession()
    vi.restoreAllMocks()
  })

  it('a harvest without yield_kg, or sold_kg without price, or sold_kg > yield_kg, blocks the save with a field error and writes nothing; a valid harvest writes one row + one outbox item', async () => {
    renderSheet()

    // Select plot
    const plotSelect = screen.getByLabelText(/Parcela/i)
    fireEvent.change(plotSelect, { target: { value: PLOTS[0].id } })

    // Select kind harvest
    const kindSelect = screen.getByLabelText(/Tipo de registro/i)
    fireEvent.change(kindSelect, { target: { value: 'harvest' } })

    // 1. Missing yield_kg -> click Guardar
    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    expect(await screen.findByText(/El rendimiento es requerido/i)).toBeInTheDocument()
    expect(await db.logbookEntries.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)

    // 2. Fill yield_kg=100, sold_kg=50, price empty
    const yieldInput = screen.getByLabelText(/Rendimiento \(kg\)/i)
    fireEvent.change(yieldInput, { target: { value: '100' } })

    const soldInput = screen.getByLabelText(/Cantidad vendida \(kg\)/i)
    fireEvent.change(soldInput, { target: { value: '50' } })

    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    expect(await screen.findByText(/Debes indicar el precio de venta si registras cantidad vendida/i)).toBeInTheDocument()
    expect(await db.logbookEntries.count()).toBe(0)

    // 3. sold_kg > yield_kg: sold_kg=150, price=2000
    fireEvent.change(soldInput, { target: { value: '150' } })
    const priceInput = screen.getByLabelText(/Precio de venta/i)
    fireEvent.change(priceInput, { target: { value: '2000' } })

    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    expect(await screen.findByText(/La cantidad vendida no puede superar el rendimiento/i)).toBeInTheDocument()
    expect(await db.logbookEntries.count()).toBe(0)

    // 4. Valid harvest: sold_kg=80, price=2000
    fireEvent.change(soldInput, { target: { value: '80' } })
    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    await waitFor(async () => {
      expect(await db.logbookEntries.count()).toBe(1)
      expect(await db.outbox.count()).toBe(1)
    })

    const [row] = await db.logbookEntries.toArray()
    expect(row.kind).toBe('harvest')
    expect(row.yield_kg).toBe(100)
    expect(row.sold_kg).toBe(80)
    expect(row.sale_price_cop_per_kg).toBe(2000)
    expect(row.labor_days).toBeNull()
    expect(row.irrigation_mm).toBeNull()
  })

  it("only the chosen kind's fields are sent (a task never carries yield_kg)", async () => {
    renderSheet()

    // Select plot
    const plotSelect = screen.getByLabelText(/Parcela/i)
    fireEvent.change(plotSelect, { target: { value: PLOTS[1].id } })

    // Select kind task
    const kindSelect = screen.getByLabelText(/Tipo de registro/i)
    fireEvent.change(kindSelect, { target: { value: 'task' } })

    // Yield field is not even rendered for a task
    expect(screen.queryByLabelText(/Rendimiento \(kg\)/i)).not.toBeInTheDocument()

    // Fill labor_days
    const laborInput = screen.getByLabelText(/Jornales/i)
    fireEvent.change(laborInput, { target: { value: '4' } })

    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    await waitFor(async () => {
      expect(await db.logbookEntries.count()).toBe(1)
      expect(await db.outbox.count()).toBe(1)
    })

    const [row] = await db.logbookEntries.toArray()
    expect(row.kind).toBe('task')
    expect(row.labor_days).toBe(4)
    expect(row.yield_kg).toBeNull()
    expect(row.sold_kg).toBeNull()
    expect(row.sale_price_cop_per_kg).toBeNull()
    expect(row.irrigation_mm).toBeNull()
    expect(row.cost_cop).toBeNull()
  })

  it('prepopulates fields when entryToEdit is provided, and saving updates the entry and replaces outbox item with pending', async () => {
    const entryId = '018f0c2a-0000-7000-8000-000000000099'
    const initialEntry = {
      id: entryId,
      org_id: ORG_ID,
      plot_id: PLOTS[0].id,
      crop_cycle_id: null,
      kind: 'harvest' as const,
      occurred_on: '2026-09-25',
      quantity: null,
      unit: null,
      cost_cop: null,
      yield_kg: 50,
      sold_kg: 100,
      sale_price_cop_per_kg: 1500,
      labor_days: null,
      irrigation_mm: null,
      alert_id: null,
      notes: 'Nota anterior',
      created_by: null,
      created_offline: true,
      client_updated_at: '2026-09-25T10:00:00.000Z',
      server_version: null,
      deleted_at: null,
      syncState: 'rejected' as const,
      syncError: 'invalid',
    }
    await db.logbookEntries.add(initialEntry)
    await db.outbox.add({
      id: entryId,
      entity: 'logbook_entry',
      op: 'upsert',
      data: initialEntry,
      client_updated_at: initialEntry.client_updated_at,
      status: 'rejected',
      error: 'invalid',
    })

    renderSheet({ entryToEdit: initialEntry })

    // Check pre-populated values
    expect(screen.getByDisplayValue('50')).toBeInTheDocument()
    expect(screen.getByDisplayValue('100')).toBeInTheDocument()
    expect(screen.getByDisplayValue('1500')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Nota anterior')).toBeInTheDocument()

    // Correct sold_kg to 40 (<= yield 50)
    const soldInput = screen.getByLabelText(/Cantidad vendida \(kg\)/i)
    fireEvent.change(soldInput, { target: { value: '40' } })

    fireEvent.click(screen.getByRole('button', { name: /Guardar/i }))

    await waitFor(async () => {
      const updated = await db.logbookEntries.get(entryId)
      expect(updated?.sold_kg).toBe(40)
      expect(updated?.syncState).toBe('pending')
      expect(updated?.syncError).toBeNull()

      const outbox = await db.outbox.where('id').equals(entryId).first()
      expect(outbox?.status).toBe('pending')
      expect(outbox?.error).toBeNull()
    })
  })
})
