/**
 * The two halves of "Indicadores de tecnificación" (docs/07:145-148): ruled
 * rows inside one inset grouped list, never a hero metric, and a figure with no
 * evidence reading as absent rather than as zero (docs/11:57).
 *
 * Presentational on purpose (docs/07 §Estructura): no fetch, no store, so each
 * rule below is pinned without a query client.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NO_DATA_THIS_MONTH, OrgIndicatorsSection, PlotIndicatorsSection, monthLabel } from './IndicatorsSection'
import type { OrgIndicators, PlotIndicators } from '../api/indicatorsApi'

const PLOT: PlotIndicators = {
  plot_id: 'plot-1',
  month: '2026-09-01',
  monitoring: 0.9,
  record_keeping: 0.5,
  decision: null,
  risk_management: 0.25,
  digital_adoption_index: 72.4,
  computed_at: '2026-10-01T07:00:00Z',
}

const ORG: OrgIndicators = {
  org_id: 'org-1',
  month: '2026-09-01',
  mean_digital_adoption_index: 68.5,
  plots_with_index: 3,
  monitored_plots_ratio: 0.75,
  harvested_cycles_ratio: null,
  median_hours_to_first_reading: null,
}

function rowFor(label: string): HTMLElement {
  const row = screen.getByText(label).closest('li')
  if (row === null) throw new Error(`no row labelled ${label}`)
  return row
}

describe('monthLabel', () => {
  it('names the stored month in plain Spanish', () => {
    expect(monthLabel('2026-09-01')).toBe('septiembre de 2026')
    expect(monthLabel('2026-01-01')).toBe('enero de 2026')
  })
})

describe('PlotIndicatorsSection', () => {
  it('shows the month index and its four components as ruled rows', () => {
    render(<PlotIndicatorsSection month="2026-09" indicators={PLOT} />)

    expect(screen.getByRole('heading', { name: 'Parcela activa' })).toBeInTheDocument()
    expect(within(rowFor('Adopción digital')).getByText('72')).toBeInTheDocument()
    expect(within(rowFor('Medición continua')).getByText('90.0 %')).toBeInTheDocument()
    expect(within(rowFor('Registro en bitácora')).getByText('50.0 %')).toBeInTheDocument()
    expect(within(rowFor('Alertas con acción')).getByText('25.0 %')).toBeInTheDocument()
  })

  it('reads a component with no evidence as absent, never as 0', () => {
    render(<PlotIndicatorsSection month="2026-09" indicators={PLOT} />)

    const row = rowFor('Decisiones con datos')
    expect(within(row).getByText(NO_DATA_THIS_MONTH)).toBeInTheDocument()
    expect(row.textContent).not.toContain('0.0 %')
    expect(row.textContent).not.toContain('0 %')
  })

  it('reads a month with no stored row as one absent line instead of five figures', () => {
    render(<PlotIndicatorsSection month="2026-09" indicators={null} />)

    expect(screen.getByText(NO_DATA_THIS_MONTH)).toBeInTheDocument()
    expect(screen.queryByText('Adopción digital')).toBeNull()
  })
})

describe('OrgIndicatorsSection', () => {
  it('shows the organization figures of the same month', () => {
    render(<OrgIndicatorsSection month="2026-09" indicators={ORG} />)

    expect(screen.getByRole('heading', { name: 'Organización' })).toBeInTheDocument()
    expect(within(rowFor('Adopción digital promedio')).getByText('69')).toBeInTheDocument()
    expect(within(rowFor('Parcelas con índice')).getByText('3')).toBeInTheDocument()
    expect(within(rowFor('Parcelas monitoreadas')).getByText('75.0 %')).toBeInTheDocument()
  })

  it('reads the two figures D-T7.1 leaves null as absent, never as 0', () => {
    render(<OrgIndicatorsSection month="2026-09" indicators={ORG} />)

    for (const label of ['Ciclos cerrados con cosecha', 'Tiempo a primera lectura']) {
      const row = rowFor(label)
      expect(within(row).getByText(NO_DATA_THIS_MONTH)).toBeInTheDocument()
      expect(row.textContent).not.toMatch(/(^|\D)0(\D|$)/)
    }
  })

  it('reads an organization month with nothing reported as one absent line', () => {
    render(<OrgIndicatorsSection month="2026-09" indicators={null} />)

    expect(screen.getByText(NO_DATA_THIS_MONTH)).toBeInTheDocument()
    expect(screen.queryByText('Adopción digital promedio')).toBeNull()
  })
})
