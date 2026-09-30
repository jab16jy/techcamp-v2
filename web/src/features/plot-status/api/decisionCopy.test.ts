/**
 * docs/07 §Mapa de pantallas, Inicio items 1–2, and ADR-0023 (the rainfed card:
 * deficit, 7-day rain, advice — never an irrigation depth, never minutes).
 *
 * These are the words the producer reads on the most important screen, so they
 * are asserted literally, and every case carries its negative: what it must NOT
 * say. A missing number is an omitted clause, never a zero.
 */
import { describe, expect, it } from 'vitest'
import {
  cycleLine,
  decisionFor,
  readRecommendationKind,
  type ActiveCycle,
  type DecisionInput,
  type Recommendation,
} from './decisionCopy'

function cycle(overrides: Partial<ActiveCycle> = {}): ActiveCycle {
  return {
    crop: { id: 1, code: 'maiz', name_es: 'Maíz' },
    stage: 'development',
    day_of_cycle: 42,
    ...overrides,
  } as ActiveCycle
}

function recommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    kind: 'irrigate',
    depth_mm: 12,
    duration_min: 40,
    advice: [],
    rationale: {},
    ...overrides,
  }
}

function input(overrides: Partial<DecisionInput> = {}): DecisionInput {
  return {
    plot: { irrigation_system: 'drip' },
    active_cycle: cycle(),
    recommendation: recommendation(),
    ...overrides,
  } as DecisionInput
}

describe('readRecommendationKind', () => {
  it('accepts every kind in the decision tree (docs/06 §5)', () => {
    expect(
      ['irrigate', 'postpone', 'not_needed', 'no_kc', 'rainfed'].map(readRecommendationKind),
    ).toEqual(['irrigate', 'postpone', 'not_needed', 'no_kc', 'rainfed'])
  })

  it('rejects a kind this build does not know instead of guessing one', () => {
    expect(readRecommendationKind('flood')).toBeNull()
    expect(readRecommendationKind('')).toBeNull()
  })
})

describe('cycleLine (Inicio item 1)', () => {
  it('names the crop, the cycle day and the stage, the way docs/07 writes it', () => {
    expect(cycleLine(cycle())).toBe('Maíz · día 42 · desarrollo')
  })

  it('omits the stage when the crop has no Kc stages (D-T0.6, kc_source = none)', () => {
    expect(cycleLine(cycle({ stage: null }))).toBe('Maíz · día 42')
  })

  it('shows only the crop when the sowing is later than today: no "día", no stage', () => {
    expect(cycleLine(cycle({ stage: null, day_of_cycle: null }))).toBe('Maíz')
  })

  it('never prints a day that is zero or negative (docs/04: the sowing day is day 1)', () => {
    // `day_of_cycle: -4` is what a future sowing used to return (#205).
    expect(cycleLine(cycle({ day_of_cycle: 0, stage: null }))).toBe('Maíz')
    expect(cycleLine(cycle({ day_of_cycle: -4, stage: null }))).toBe('Maíz')
  })

  it('omits a stage key this build does not know instead of leaking the raw key', () => {
    expect(cycleLine(cycle({ stage: 'flowering' as never }))).toBe('Maíz · día 42')
  })

  it('has no line at all without an active cycle', () => {
    expect(cycleLine(null)).toBeNull()
  })
})

describe('decisionFor — irrigated plots (docs/07 item 2)', () => {
  it('says the depth and the minutes, and its why carries the numbers', () => {
    const decision = decisionFor(
      input({
        recommendation: recommendation({
          rationale: { depletion_mm: 11, raw_mm: 14, et0_mm: 4.2, forecast_rain_48h_mm: 1 },
        }),
      }),
    )

    expect(decision.status).toBe('irrigate')
    expect(decision.message).toBe('Hoy: regar 12 mm (≈ 40 min)')
    expect(decision.rationale).toContain('11 mm')
    expect(decision.rationale).toContain('14 mm')
    // No depth or minutes leak into a rainfed-only field, and no advice exists here.
    expect(decision.rain).toBeNull()
    expect(decision.advice).toBeNull()
  })

  it('omits the minutes when the plot has no flow rate, rather than inventing zero', () => {
    const decision = decisionFor(input({ recommendation: recommendation({ duration_min: null }) }))

    expect(decision.message).toBe('Hoy: regar 12 mm')
    expect(decision.message).not.toContain('min')
  })

  it('reports postponing when the 48 h rain covers the deficit', () => {
    const decision = decisionFor(
      input({
        recommendation: recommendation({
          kind: 'postpone',
          depth_mm: null,
          duration_min: null,
          rationale: { depletion_mm: 6, raw_mm: 5, forecast_rain_48h_mm: 9 },
        }),
      }),
    )

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Hoy: aplaza el riego, se espera lluvia')
    expect(decision.rationale).toContain('9 mm')
    // Negative: a postponed plot is not told to irrigate, and gets no depth.
    expect(decision.message).not.toContain('regar 12')
    expect(decision.message).not.toContain('mm')
  })

  it('says the plot needs no water when the deficit is under the reserve', () => {
    const decision = decisionFor(
      input({
        recommendation: recommendation({
          kind: 'not_needed',
          depth_mm: null,
          duration_min: null,
          rationale: { depletion_mm: 2, raw_mm: 9 },
        }),
      }),
    )

    expect(decision.status).toBe('ok')
    expect(decision.message).toBe('Hoy no necesita riego')
    expect(decision.message).not.toContain('Hoy: regar')
  })

  it('admits a crop with no Kc stages instead of recommending water', () => {
    const decision = decisionFor(
      input({
        recommendation: recommendation({
          kind: 'no_kc',
          depth_mm: null,
          duration_min: null,
          rationale: { kc_source: 'none' },
        }),
      }),
    )

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Hoy: no hay datos del cultivo para decidir el riego')
    // Negative: `no_kc` is a missing input, so no amount and no minutes anywhere.
    expect(decision.message).not.toContain('mm')
    expect(decision.message).not.toContain('0')
    expect(decision.rationale).toContain('Kc')
  })

  it('treats an irrigate without a depth as no recommendation at all (never "0 mm")', () => {
    const decision = decisionFor(input({ recommendation: recommendation({ depth_mm: null }) }))

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Aún no hay recomendación para hoy')
    expect(decision.message).not.toContain('0 mm')
  })
})

describe('decisionFor — rainfed plots (ADR-0023)', () => {
  const rainfed = (overrides: Partial<Recommendation> = {}) =>
    input({
      plot: { irrigation_system: 'none' },
      recommendation: recommendation({
        kind: 'rainfed',
        depth_mm: null,
        duration_min: null,
        advice: ['conserve_moisture'],
        rationale: {
          depletion_mm: 80,
          raw_mm: 60,
          forecast_rain_7d_mm: 12,
          forecast_et0_7d_mm: 20,
        },
        ...overrides,
      }),
    })

  it('shows the deficit, the 7-day rain and the advice, and no depth or minutes', () => {
    const decision = decisionFor(rainfed())

    expect(decision.status).toBe('stress')
    expect(decision.message).toBe('Al cultivo le faltan 80 mm: está en estrés')
    expect(decision.rain).toBe('Se espera 12 mm de lluvia en los próximos 7 días.')
    expect(decision.advice).toBe('Cubra el suelo con rastrojo para conservar la humedad.')
    // ADR-0023: a rainfed plot never shows an irrigation depth nor minutes.
    expect(decision.message).not.toContain('regar')
    expect(decision.message).not.toContain('min')
  })

  it('does not claim stress at the reserve limit, where the stress starts above it (ADR-0022)', () => {
    const decision = decisionFor(rainfed({ rationale: { depletion_mm: 60, raw_mm: 60, forecast_rain_7d_mm: 12 } }))

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Al cultivo le faltan 60 mm')
    expect(decision.message).not.toContain('estrés')
  })

  it('reads a plot with reserve left as well watered', () => {
    const decision = decisionFor(rainfed({ rationale: { depletion_mm: 10, raw_mm: 60, forecast_rain_7d_mm: 12 } }))

    expect(decision.status).toBe('ok')
    expect(decision.message).toBe('Al cultivo le faltan 10 mm')
  })

  it('each advice code gets its own sentence of the day', () => {
    const adviceFor = (code: string) =>
      decisionFor(rainfed({ advice: [code] })).advice

    expect(adviceFor('delay_sowing')).toBe(
      'Aplaza la siembra: se espera menos lluvia que la que el cultivo necesita.',
    )
    expect(adviceFor('rain_expected')).toBe(
      'Espera la lluvia antes de intervenir: se espera que cubra el déficit.',
    )
    expect(adviceFor('conserve_moisture')).toBe(
      'Cubra el suelo con rastrojo para conservar la humedad.',
    )
    expect(adviceFor('prioritize_harvest')).toBe(
      'Prioriza la cosecha: el cultivo ya está en etapa final.',
    )
    expect(adviceFor('no_action')).toBe('Por hoy no hace falta ninguna acción en esta parcela.')
  })

  it('never turns an advice code into a depth or a duration', () => {
    for (const code of [
      'delay_sowing',
      'rain_expected',
      'conserve_moisture',
      'prioritize_harvest',
      'no_action',
    ]) {
      expect(decisionFor(rainfed({ advice: [code] })).advice).not.toMatch(/\d/)
    }
  })

  it('says plainly that there is no advice when the server sent none', () => {
    // `evaluate_rainfed_advice` returns no code when a plot without a cycle sees
    // 7-day rain >= 7-day ET0 — an empty list, which is not the `no_action` code.
    const decision = decisionFor(rainfed({ advice: [] }))

    expect(decision.advice).toBe('Hoy no hay consejo para esta parcela.')
    expect(decision.advice).not.toBe('Por hoy no hace falta ninguna acción en esta parcela.')
  })

  it('does not invent advice for a code this build does not know', () => {
    const decision = decisionFor(rainfed({ advice: ['migrate_the_plot'] }))

    expect(decision.advice).toBe('Hoy no hay consejo para esta parcela.')
  })

  it('drops the stress and rain claims it has no number for, without faking one', () => {
    const decision = decisionFor(rainfed({ rationale: {} }))

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Hoy no hay datos suficientes para decidir')
    expect(decision.rain).toBeNull()
    expect(decision.message).not.toMatch(/\d/)
  })
})

describe('decisionFor — no recommendation at all', () => {
  it('admits there is no recommendation today instead of a fake one', () => {
    const decision = decisionFor(input({ recommendation: null }))

    expect(decision.status).toBe('watch')
    expect(decision.message).toBe('Aún no hay recomendación para hoy')
    expect(decision.rain).toBeNull()
    expect(decision.advice).toBeNull()
    expect(decision.rationale).toContain('una vez al día')
  })

  it('says the same for a kind this build does not know', () => {
    const decision = decisionFor(
      input({ recommendation: recommendation({ kind: 'flood', depth_mm: null, duration_min: null }) }),
    )

    expect(decision.message).toBe('Aún no hay recomendación para hoy')
    expect(decision.message).not.toContain('Hoy: regar')
  })
})

describe('the "why" (D-T0.11: written from the stored rationale object)', () => {
  const why = (rationale: Record<string, unknown>) =>
    decisionFor(input({ recommendation: recommendation({ rationale }) })).rationale

  it('shows the evidence flags the calculation carries, honestly', () => {
    const text = why({
      depletion_mm: 11,
      raw_mm: 14,
      kc_approximate: true,
      without_sensor: true,
      low_confidence: true,
      forecast_missing: true,
    })

    expect(text).toContain('11 mm')
    expect(text).toContain('14 mm')
    expect(text).toContain('aproximado')
    expect(text).toContain('sensor')
    expect(text).toContain('baja confianza')
    expect(text).toContain('pronóstico')
  })

  it('omits a flag the calculation did not raise, instead of showing it as false', () => {
    const text = why({ depletion_mm: 11, raw_mm: 14 })

    expect(text).not.toContain('baja confianza')
    expect(text).not.toContain('pronóstico')
    expect(text).not.toContain('aproximado')
  })

  it('omits every number it does not have, and never prints a zero for one', () => {
    const text = why({})

    expect(text).not.toContain('0 mm')
    expect(text).not.toContain('NaN')
    expect(text.trim().length).toBeGreaterThan(0)
  })

  it('ignores a non-numeric value in the rationale rather than rendering it', () => {
    const text = why({ depletion_mm: 'doce', raw_mm: null, et0_mm: 4 })

    expect(text).toContain('4 mm')
    expect(text).not.toContain('doce')
    expect(text).not.toContain('null')
  })
})
