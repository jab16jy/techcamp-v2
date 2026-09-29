import 'fake-indexeddb/auto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from '../../../lib/db/db'
import { resetLocalDb } from '../../../lib/db/testDb'
import { NewVisitSheet } from './NewVisitSheet'

const ORG_ID = '018f0c2a-0000-7000-8000-0000000000aa'
const FARM_ID = '018f0c2a-0000-7000-8000-0000000000cc'
const TECHNICIAN_ID = '018f0c2a-0000-7000-8000-0000000000dd'

const mockFarm = {
  id: FARM_ID,
  org_id: ORG_ID,
  name: 'Finca La Esperanza',
}

const mockPlots = [
  { id: 'plot-1', name: 'Lote Norte' },
  { id: 'plot-2', name: 'Lote Sur' },
]

function renderSheet(props: Partial<React.ComponentProps<typeof NewVisitSheet>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenChange = vi.fn()
  const result = render(
    <QueryClientProvider client={queryClient}>
      <NewVisitSheet
        open={true}
        onOpenChange={onOpenChange}
        farm={mockFarm}
        plots={mockPlots}
        technicianId={TECHNICIAN_ID}
        {...props}
      />
    </QueryClientProvider>,
  )
  return { ...result, onOpenChange }
}

describe('NewVisitSheet', () => {
  beforeEach(async () => {
    await resetLocalDb()
    vi.stubGlobal('navigator', { ...navigator, onLine: true })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('saving writes one extensionVisits row AND one outbox item with chosen topics, technician_id and farm_id; saves with network offline', async () => {
    // Set network offline (navigator.onLine = false)
    vi.stubGlobal('navigator', { ...navigator, onLine: false })

    // Negative assertion: initially nothing is stored
    expect(await db.extensionVisits.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)

    const { onOpenChange } = renderSheet()

    // Title and form fields are present
    expect(screen.getByText('Nueva visita de extensión')).toBeInTheDocument()

    // Select topics
    const humanTopic = screen.getByLabelText(/Capacidades humanas integrales/i)
    const naturalTopic = screen.getByLabelText(/Gestión sostenible de los recursos naturales/i)
    fireEvent.click(humanTopic)
    fireEvent.click(naturalTopic)

    // Fill notes, recommendations and commitments
    fireEvent.change(screen.getByLabelText(/^Recomendaciones/i), {
      target: { value: 'Ajustar frecuencia de riego' },
    })
    fireEvent.change(screen.getByLabelText(/^Compromisos/i), {
      target: { value: 'Revisión en 15 días' },
    })
    fireEvent.change(screen.getByLabelText(/^Notas/i), {
      target: { value: 'Buen estado vegetativo general' },
    })

    // Submit the visit
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    // Sheet closes
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))

    // Exactly one row in extensionVisits and one item in outbox
    const visits = await db.extensionVisits.toArray()
    expect(visits).toHaveLength(1)
    expect(visits[0].org_id).toBe(ORG_ID)
    expect(visits[0].farm_id).toBe(FARM_ID)
    expect(visits[0].technician_id).toBe(TECHNICIAN_ID)
    expect(visits[0].topics).toEqual(['human_capacities', 'natural_resources'])
    expect(visits[0].recommendations).toBe('Ajustar frecuencia de riego')
    expect(visits[0].commitments).toBe('Revisión en 15 días')
    expect(visits[0].notes).toBe('Buen estado vegetativo general')
    expect(visits[0].syncState).toBe('pending')

    const outboxItems = await db.outbox.toArray()
    expect(outboxItems).toHaveLength(1)
    expect(outboxItems[0].entity).toBe('extension_visit')
    expect(outboxItems[0].op).toBe('upsert')
    expect(outboxItems[0].id).toBe(visits[0].id)
    expect(outboxItems[0].data).toMatchObject({
      farm_id: FARM_ID,
      technician_id: TECHNICIAN_ID,
      topics: ['human_capacities', 'natural_resources'],
    })
  })

  it('no topic selected → save is blocked with visible field error, and nothing is written', async () => {
    // Negative assertion: initially empty
    expect(await db.extensionVisits.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)

    const { onOpenChange } = renderSheet()

    // No topic clicked - try to submit
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    // Visible field error is shown
    expect(await screen.findByText('Selecciona al menos un tema de la visita.')).toBeInTheDocument()

    // Negative assertion: sheet did NOT close and nothing was written to IndexedDB
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    expect(await db.extensionVisits.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)
  })

  it('a plot outside the farm cannot be chosen (only the farm plots are offered)', async () => {
    renderSheet({
      plots: [
        { id: 'plot-farm-1', name: 'Parcela Uno' },
        { id: 'plot-farm-2', name: 'Parcela Dos' },
      ],
    })

    // Click plot trigger to open select dropdown
    const plotSelect = screen.getByRole('combobox', { name: /Parcela \(opcional\)/i })
    expect(plotSelect).toBeInTheDocument()
    fireEvent.click(plotSelect)

    // Parcela Uno and Parcela Dos are offered, but not an outside plot
    expect(screen.queryByText('Parcela De Otra Finca')).not.toBeInTheDocument()

    // Farm's plots are rendered as options
    expect(screen.getByRole('option', { name: 'Parcela Uno' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Parcela Dos' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Sin parcela específica/i })).toBeInTheDocument()

    // Select Parcela Uno
    fireEvent.click(screen.getByRole('option', { name: 'Parcela Uno' }))

    // Select a topic and save
    fireEvent.click(screen.getByLabelText(/Capacidades sociales y asociatividad/i))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    await waitFor(async () => {
      const visits = await db.extensionVisits.toArray()
      expect(visits).toHaveLength(1)
      expect(visits[0].plot_id).toBe('plot-farm-1')
      expect(visits[0].topics).toEqual(['social_capacities'])
    })
  })
})
