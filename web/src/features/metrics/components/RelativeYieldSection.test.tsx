/**
 * The relative yield line (D-DH.11): a dash and the reason when the field is
 * `null`, never a zero and never a bare dash.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { CycleSummary } from '../api/cycleSummaryApi'
import { NO_MUNICIPAL_REFERENCE, RelativeYieldSection } from './RelativeYieldSection'

const SUMMARY: CycleSummary = {
  crop_cycle_id: '01a175de-882f-7d2d-a1b9-b5956fb624d4',
  plot_id: '01a175de-879b-763b-9f01-587640805f6a',
  org_id: '01a175de-86f0-7ccd-8c04-9d6b4a2f2f31',
  cycle_status: 'active',
  yield_kg_ha: 3500,
  yield_change_vs_baseline: null,
  relative_yield: null,
  water_applied_m3_ha: null,
  water_stress_days: null,
  irrigation_wue_kg_m3: null,
  cost_cop_ha: null,
  cost_cop_kg: null,
  yield_kg_per_labor_day: null,
  gross_margin_cop: null,
  loss_kg: null,
  loss_cop: null,
  computed_at: '2026-10-01T07:00:00Z',
}

describe('RelativeYieldSection', () => {
  it('shows the dash and the reason when the relative yield is null', () => {
    render(<RelativeYieldSection summary={SUMMARY} />)

    expect(screen.getByRole('heading', { name: 'Ciclo actual' })).toBeInTheDocument()
    expect(screen.getByText('Rendimiento relativo municipal')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText(NO_MUNICIPAL_REFERENCE)).toBeInTheDocument()
  })

  it('never shows a zero for a figure nobody measured', () => {
    render(<RelativeYieldSection summary={SUMMARY} />)

    expect(screen.queryByText('0')).toBeNull()
    expect(screen.queryByText('0.00')).toBeNull()
  })

  it('shows the figure and no reason when there is one', () => {
    render(<RelativeYieldSection summary={{ ...SUMMARY, relative_yield: 1.25 }} />)

    expect(screen.getByText('1.25')).toBeInTheDocument()
    expect(screen.queryByText(NO_MUNICIPAL_REFERENCE)).toBeNull()
  })

  it('says nothing at all when the plot has no active cycle', () => {
    render(<RelativeYieldSection summary={null} />)

    expect(screen.queryByRole('heading', { name: 'Ciclo actual' })).toBeNull()
    expect(screen.queryByText('Rendimiento relativo municipal')).toBeNull()
  })
})
