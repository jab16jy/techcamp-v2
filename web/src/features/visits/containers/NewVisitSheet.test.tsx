import 'fake-indexeddb/auto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from '../../../lib/db/db'
import { resetLocalDb } from '../../../lib/db/testDb'
import { NewVisitSheet } from './NewVisitSheet'

// jsdom has no canvas and no `createImageBitmap`, so the real compressor cannot
// run here. Everything else — the sheet, the store, the attachment — is real.
vi.mock('../../../lib/photos/browser', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../lib/photos/browser')>()
  const { encodedPhoto } = await import('../../../lib/photos/compress')
  return {
    ...actual,
    compressPhoto: async () => encodedPhoto(new Uint8Array(new ArrayBuffer(2048)), 'image/jpeg'),
  }
})

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
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('saving writes one extensionVisits row AND one outbox item with chosen topics, technician_id and farm_id', async () => {
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

  it('a chosen photo is attached to the visit that was saved, and never before', async () => {
    const { onOpenChange } = renderSheet()
    fireEvent.click(screen.getByLabelText(/Capacidades humanas integrales/i))

    fireEvent.change(screen.getByLabelText('Agregar foto'), {
      target: { files: [new File(['x'], 'visita.jpg', { type: 'image/jpeg' })] },
    })

    // It is on screen as pending, and nowhere in the store yet: the visit it
    // belongs to does not exist until the user saves.
    expect(await screen.findByText('Pendiente de subir')).toBeInTheDocument()
    expect(await db.photos.count()).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))

    const visits = await db.extensionVisits.toArray()
    const photos = await db.photos.toArray()
    expect(photos).toHaveLength(1)
    expect(photos[0].entity).toBe('extension_visit')
    expect(photos[0].parent_id).toBe(visits[0].id)
    expect(photos[0].status).toBe('pending')
    expect(photos[0].bytes).toBe(2048)
  })

  it('no topic selected → save is blocked with visible field error, and nothing is written', async () => {
    // Negative assertion: initially empty
    expect(await db.extensionVisits.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)

    const { onOpenChange } = renderSheet()

    // No topic clicked - try to submit
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    // Visible field error is shown under topics
    expect(await screen.findByText('Selecciona al menos un tema de la visita.')).toBeInTheDocument()

    // Negative assertion: sheet did NOT close and nothing was written to IndexedDB
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    expect(await db.extensionVisits.count()).toBe(0)
    expect(await db.outbox.count()).toBe(0)
  })

  it('a plot outside the farm cannot be chosen (only the farm plots are offered)', async () => {
    const farmPlots = [
      { id: 'plot-farm-1', name: 'Parcela Uno' },
      { id: 'plot-farm-2', name: 'Parcela Dos' },
    ]
    const outsidePlot = { id: 'plot-outside', name: 'Lote Ajeno de Otra Finca' }

    renderSheet({
      plots: farmPlots,
    })

    // Click plot trigger to open select dropdown
    const plotSelect = screen.getByRole('combobox', { name: /Parcela \(opcional\)/i })
    expect(plotSelect).toBeInTheDocument()
    fireEvent.click(plotSelect)

    // Farm's plots are rendered as options
    expect(screen.getByRole('option', { name: 'Parcela Uno' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Parcela Dos' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Sin parcela específica/i })).toBeInTheDocument()

    // Outside plot supplied to the environment is not offered in the options
    expect(screen.queryByRole('option', { name: outsidePlot.name })).not.toBeInTheDocument()

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

  it('when Dexie fails to save, shows a visible Spanish error, keeps the sheet open with typed data, and does not produce an unhandled rejection', async () => {
    // Force db.transaction to fail (simulating QuotaExceededError or Dexie error)
    vi.spyOn(db, 'transaction').mockRejectedValueOnce(new Error('QuotaExceededError'))

    const { onOpenChange } = renderSheet()

    // Select a topic and enter recommendations
    fireEvent.click(screen.getByLabelText(/Capacidades humanas integrales/i))
    fireEvent.change(screen.getByLabelText(/^Recomendaciones/i), {
      target: { value: 'Revisión urgente de canal de drenaje' },
    })

    // Click submit
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    // Visible Spanish error is displayed in an alert
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/No se pudo guardar la visita en el teléfono/i)

    // Sheet remains open (onOpenChange(false) was NOT called)
    expect(onOpenChange).not.toHaveBeenCalledWith(false)

    // Typed data is preserved in the inputs
    expect(screen.getByLabelText(/^Recomendaciones/i)).toHaveValue('Revisión urgente de canal de drenaje')
  })

  it('missing technician displays in dedicated form-level error slot and is not cleared by toggling topics', async () => {
    // Render without a technicianId and without me data
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <NewVisitSheet
          open={true}
          onOpenChange={vi.fn()}
          farm={mockFarm}
          plots={mockPlots}
          technicianId={undefined}
        />
      </QueryClientProvider>,
    )

    // Select a topic
    fireEvent.click(screen.getByLabelText(/Capacidades humanas integrales/i))

    // Submit
    fireEvent.click(screen.getByRole('button', { name: 'Guardar visita' }))

    // Dedicated form-level error slot displays the technician error
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('No se pudo identificar al técnico que registra la visita.')

    // Toggle another topic: dedicated form-level error remains, topicError is not touched
    fireEvent.click(screen.getByLabelText(/Gestión sostenible de los recursos naturales/i))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'No se pudo identificar al técnico que registra la visita.',
    )
    expect(screen.queryByText('Selecciona al menos un tema de la visita.')).not.toBeInTheDocument()
  })
})
