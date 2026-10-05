/**
 * docs/07:152 — Inicio shows `digital_adoption_index` as a discreet line with
 * its month ("Adopción digital: 72 · septiembre"), and nothing at all when it is
 * `null`.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PlotHeader } from './PlotHeader'

describe('PlotHeader', () => {
  it('shows the plot name and its crop line', () => {
    render(<PlotHeader plotName="Lote Norte" cycleLine="Maíz · día 42 · desarrollo" adoptionIndex={null} />)

    expect(screen.getByRole('heading', { name: 'Lote Norte' })).toBeInTheDocument()
    expect(screen.getByText('Maíz · día 42 · desarrollo')).toBeInTheDocument()
  })

  it('shows the adoption index with its month', () => {
    render(
      <PlotHeader
        plotName="Lote Norte"
        cycleLine={null}
        adoptionIndex={{ value: 72, month: '2026-09-01' }}
      />,
    )

    expect(screen.getByText('Adopción digital: 72 · septiembre')).toBeInTheDocument()
  })

  it('rounds the index and reads the month from its own bucket, not the local day', () => {
    render(
      <PlotHeader
        plotName="Lote Norte"
        cycleLine={null}
        adoptionIndex={{ value: 72.4, month: '2026-09-01' }}
      />,
    )

    // The stored month is a date-only bucket read as UTC; formatting it in a
    // UTC-5 device's own zone would shift it back to August.
    expect(screen.getByText('Adopción digital: 72 · septiembre')).toBeInTheDocument()
  })

  it('says nothing about adoption when the plot has no index yet', () => {
    render(<PlotHeader plotName="Lote Norte" cycleLine={null} adoptionIndex={null} />)

    expect(screen.queryByText(/Adopción digital/)).toBeNull()
  })

  it('omits the crop line whole rather than half-written', () => {
    render(<PlotHeader plotName="Lote Norte" cycleLine={null} adoptionIndex={{ value: 40, month: '2026-08-01' }} />)

    expect(screen.getByText('Adopción digital: 40 · agosto')).toBeInTheDocument()
    expect(screen.queryByText(/día/)).toBeNull()
  })
})
