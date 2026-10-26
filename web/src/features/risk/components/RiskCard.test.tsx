/**
 * The risk card: the event, the probability, the horizon, why the model says
 * it, and where the number comes from (docs/07 §Riesgo climático).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { riskEventLine, type RiskPrediction } from '../api/riskCopy'
import { RiskCard } from './RiskCard'

const BASELINE: RiskPrediction = {
  event_type: 'flood',
  horizon_start: '2026-11-01',
  horizon_days: 30,
  probability: 0.08547,
  severity: 'low',
  top_factors: [{ feature: 'precip_sum_6m', value: 241, contribution: 0.31 }],
  model_version: {
    name: 'climatology_month',
    version: '1',
    is_baseline: true,
    metrics: null,
  },
  created_at: '2026-10-01T06:00:00Z',
}

const CALIBRATED: RiskPrediction = {
  ...BASELINE,
  severity: 'high',
  model_version: { name: 'risk_flood', version: '3', is_baseline: false, metrics: null },
}

function renderCard(prediction: RiskPrediction) {
  const event = riskEventLine(prediction)
  if (event === null) throw new Error('the card needs an event it can name')
  return render(<RiskCard event={event} />)
}

describe('RiskCard', () => {
  it('shows the event, its probability and its horizon', () => {
    renderCard(BASELINE)

    expect(screen.getByRole('heading', { name: 'Inundación' })).toBeInTheDocument()
    expect(screen.getByText('8.5 %')).toBeInTheDocument()
    expect(screen.getByText('1 de noviembre → 30 de noviembre · 30 días')).toBeInTheDocument()
  })

  it('shows why the model says it, with the feature and its value', () => {
    renderCard(BASELINE)

    expect(screen.getByText('Por qué')).toBeInTheDocument()
    expect(screen.getByText('Lluvia acumulada (6 meses)')).toBeInTheDocument()
    expect(screen.getByText('241 mm')).toBeInTheDocument()
  })

  it('does not declare a severity for the line base', () => {
    renderCard(BASELINE)

    expect(screen.queryByText('Severidad')).toBeNull()
    expect(screen.queryByText('Riesgo bajo')).toBeNull()
  })

  it('says where the number comes from', () => {
    renderCard(BASELINE)

    expect(
      screen.getByText('Origen: climatology_month v1 · línea base · sin cortes calibrados'),
    ).toBeInTheDocument()
  })

  it('declares the severity of a calibrated version', () => {
    renderCard(CALIBRATED)

    expect(screen.getByText('Severidad')).toBeInTheDocument()
    expect(screen.getByText('Riesgo alto')).toBeInTheDocument()
  })
})
