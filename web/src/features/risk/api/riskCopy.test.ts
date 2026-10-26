/**
 * The risk card's copy decisions (D-DH.12, D-T9.2): a version without
 * calibrated cuts does not get a severity, the origin of the number is always
 * named, and an event this build does not know is left out whole.
 */
import { describe, expect, it } from 'vitest'
import {
  eventName,
  factorLine,
  horizonLine,
  originLine,
  riskEventLine,
  riskEventLines,
  severityWord,
  type RiskPrediction,
} from './riskCopy'

const BASELINE: RiskPrediction = {
  event_type: 'flood',
  horizon_start: '2026-11-01',
  horizon_days: 30,
  probability: 0.08547,
  severity: 'low',
  top_factors: [
    { feature: 'precip_anomaly_3m', value: -12.4, contribution: 0.31 },
    { feature: 'precip_sum_6m', value: 241, contribution: 0.22 },
  ],
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

describe('eventName', () => {
  it('names the two events the risk module serves', () => {
    expect(eventName('flood')).toBe('Inundación')
    expect(eventName('drought')).toBe('Sequía')
  })

  it('leaves an event it does not know unnamed', () => {
    expect(eventName('earthquake')).toBeNull()
  })
})

describe('severityWord', () => {
  it('reads the risk severities in plain words', () => {
    expect(severityWord('low')).toBe('Riesgo bajo')
    expect(severityWord('high')).toBe('Riesgo alto')
    expect(severityWord('critical')).toBe('Riesgo crítico')
  })

  it('leaves a severity it does not know unnamed', () => {
    expect(severityWord('moderate')).toBeNull()
  })
})

describe('originLine', () => {
  it('names the line base and the cuts it does not have', () => {
    expect(originLine({ name: 'climatology_month', version: '1', is_baseline: true })).toBe(
      'climatology_month v1 · línea base · sin cortes calibrados',
    )
  })

  it('names a calibrated version as such', () => {
    expect(originLine({ name: 'risk_flood', version: '3', is_baseline: false })).toBe(
      'risk_flood v3 · modelo calibrado',
    )
  })
})

describe('factorLine', () => {
  it('names the feature in plain words and adds its unit', () => {
    expect(factorLine({ feature: 'precip_sum_6m', value: 241 })).toEqual({
      label: 'Lluvia acumulada (6 meses)',
      value: '241 mm',
    })
  })

  it('keeps the model name of a feature it does not know, with no unit', () => {
    expect(factorLine({ feature: 'river_distance_m', value: 12 })).toEqual({
      label: 'river_distance_m',
      value: '12',
    })
  })

  it('says nothing about a value the model did not have', () => {
    expect(factorLine({ feature: 'precip_sum_6m', value: null }).value).toBe('—')
  })
})

describe('horizonLine', () => {
  it('names the first day, the last one and how many days it covers', () => {
    expect(horizonLine('2026-11-01', 30)).toBe('1 de noviembre → 30 de noviembre · 30 días')
  })

  it('reads a one-day horizon in the singular', () => {
    expect(horizonLine('2026-11-01', 1)).toBe('1 de noviembre → 1 de noviembre · 1 día')
  })
})

describe('riskEventLine', () => {
  it('does not declare a severity for the line base', () => {
    // The served line base is registered with `thresholds={}`, so its severity
    // is always `low` and says nothing about the risk (D-T9.2, D-DH.12).
    expect(riskEventLine(BASELINE)?.severity).toBeNull()
  })

  it('declares the severity of a calibrated version', () => {
    expect(riskEventLine(CALIBRATED)?.severity).toBe('Riesgo alto')
  })

  it('reads the probability as a percentage', () => {
    expect(riskEventLine(BASELINE)?.probability).toBe('8.5 %')
  })

  it('names the origin of the number', () => {
    expect(riskEventLine(BASELINE)?.origin).toBe(
      'climatology_month v1 · línea base · sin cortes calibrados',
    )
  })

  it('leaves an event it does not know without a card', () => {
    expect(riskEventLine({ ...BASELINE, event_type: 'earthquake' })).toBeNull()
  })
})

describe('riskEventLines', () => {
  it('drops the events it cannot name and keeps the rest', () => {
    const lines = riskEventLines([
      BASELINE,
      { ...BASELINE, event_type: 'earthquake' },
      { ...BASELINE, event_type: 'drought' },
    ])

    expect(lines.map((line) => line.event)).toEqual(['Inundación', 'Sequía'])
  })
})
