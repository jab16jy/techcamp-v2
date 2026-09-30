/**
 * The words of the rest of the home screen (docs/07 §Mapa de pantallas, Inicio
 * items 3–5): the open alerts, the soil-moisture reading, the 3-day forecast and
 * the node rows.
 *
 * Every alert the factory ships (docs/06 §3, docs/03 §Reglas de fábrica) has a
 * Spanish name here, taken from the rule's own condition, and no rule invents a
 * number the server did not send. Missing evidence is a dash or a sentence, never
 * a zero.
 */
import { describe, expect, it } from 'vitest'
import type { components } from '../../../lib/api/schema'
import {
  NO_OPEN_ALERTS,
  alertView,
  forecastRows,
  nodeStateLabel,
  soilMoisture,
  type OpenAlert,
  type WeatherDay,
} from './statusCopy'

function alert(overrides: Partial<OpenAlert> = {}): OpenAlert {
  return {
    id: 'alert-1',
    org_id: 'org-1',
    rule_id: 'rule-1',
    rule_code: 'water_stress',
    plot_id: 'plot-1',
    node_id: null,
    state: 'open',
    severity: 'warning',
    evidence: {},
    opened_at: '2026-09-30T12:00:00Z',
    acknowledged_at: null,
    resolved_at: null,
    escalated_at: null,
    resolution_note: null,
    ...overrides,
  } as OpenAlert
}

function day(day: string, overrides: Partial<WeatherDay> = {}): WeatherDay {
  return {
    day,
    is_forecast: true,
    et0_mm: 4,
    rain_mm: 12,
    tmin_c: 24,
    tmax_c: 31,
    rh_mean_pct: 70,
    fetched_at: '2026-09-30T06:00:00Z',
    stale: false,
    ...overrides,
  } as WeatherDay
}

describe('alertView (Inicio item 3)', () => {
  it('names every factory rule in Spanish, from the rule code (docs/06 §3)', () => {
    const codes = [
      'water_stress',
      'waterlogging',
      'heat_stress',
      'fungal_risk',
      'heavy_rain_forecast',
      'flood_risk',
      'drought_risk',
      'node_offline',
      'node_battery_low',
    ]

    for (const ruleCode of codes) {
      const view = alertView(alert({ rule_code: ruleCode }))
      expect(view.title.length).toBeGreaterThan(0)
      // Negative: the raw code never reaches the screen as the title.
      expect(view.title).not.toBe(ruleCode)
    }
  })

  it('carries the server severity through, and the colour never stands alone', () => {
    expect(alertView(alert({ severity: 'critical' })).severity).toBe('critical')
    expect(alertView(alert({ severity: 'info' })).severity).toBe('info')
    expect(alertView(alert({ severity: 'warning' })).severity).toBe('warning')
  })

  it('shows a severity this build does not know as a warning, never as information', () => {
    // Downgrading an unrecognized alert to "Información" would understate a
    // possibly critical event; overstating the band is the safe direction here.
    expect(alertView(alert({ severity: 'apocalipsis' })).severity).toBe('warning')
  })

  it('describes the condition without inventing a measurement', () => {
    const view = alertView(alert({ rule_code: 'heat_stress', evidence: { value: 41.2 } }))

    expect(view.description).toBe('La temperatura del aire lleva más de 3 h por encima de 35 °C.')
    // Negative: the evidence object is not a contract the home guesses at.
    expect(view.description).not.toContain('41,2')
    expect(view.description).not.toContain('undefined')
  })

  it('keeps the raw code when the rule is one this build does not know', () => {
    // An organization may add its own rules (docs/04 §Alertas), so the code is
    // the only truthful label available.
    const view = alertView(alert({ rule_code: 'mi_regla' }))

    expect(view.title).toBe('mi_regla')
    expect(view.description).toBe('Revisa el detalle de la alerta.')
  })

  it('has a quiet line for the case where the plot has no open alerts', () => {
    expect(NO_OPEN_ALERTS).toBe('Sin alertas abiertas.')
  })
})

type WaterBalance = NonNullable<components['schemas']['PlotStatusView']['water_balance']>

/** The plot's θ_estrés, the only threshold `/status` carries for the reading. */
function balance(stressMoisturePct: number): WaterBalance {
  return {
    depletion_mm: 11,
    taw_mm: 100,
    raw_mm: 14,
    stress_moisture_pct: stressMoisturePct,
    status: 'ok',
  }
}

describe('soilMoisture (Inicio item 4)', () => {
  const latest = (overrides: Record<string, unknown> = {}) => ({
    soil_moisture_pct: 31.2,
    air_temp_c: 27,
    air_rh_pct: 80,
    at: '2026-09-30T12:00:00Z',
    ...overrides,
  })

  it('reads the percentage when the plot has a reading', () => {
    expect(soilMoisture(latest(), null).value).toBe('31,2 %')
  })

  it('says so when there is no reading, never a zero percent', () => {
    const reading = soilMoisture(latest({ soil_moisture_pct: null }), null)

    expect(reading.value).toBe('Sin lectura')
    // Negative: a missing reading is not 0 % and not a confident "Bien".
    expect(reading.value).not.toContain('0')
    expect(reading.status).toBe('watch')
  })

  it('is not in stress while the reading sits above the crop stress threshold', () => {
    // θ_estrés is the only threshold `/status` carries, so without it the home
    // does not judge a percentage it has nothing to compare against.
    expect(soilMoisture(latest({ soil_moisture_pct: 40 }), balance(15)).status).toBe('ok')
    expect(soilMoisture(latest({ soil_moisture_pct: 12 }), balance(15)).status).toBe('stress')
    // Negative: no threshold, no verdict.
    expect(soilMoisture(latest({ soil_moisture_pct: 12 }), null).status).toBe('ok')
  })

  it('claims the plot is in stress only when the balance proves it below θ_estrés', () => {
    // 12 % under a 15 % threshold is stressed; 12 % over a 10 % threshold is not.
    const reading = soilMoisture(latest({ soil_moisture_pct: 12 }), balance(15))

    expect(reading.status).toBe('stress')

    const aboveThreshold = soilMoisture(latest({ soil_moisture_pct: 12 }), balance(10))
    expect(aboveThreshold.status).not.toBe('stress')
  })

  it('is a stress reading exactly at θ_estrés, because the stress starts below it', () => {
    const reading = soilMoisture(latest({ soil_moisture_pct: 15 }), balance(15))

    expect(reading.status).toBe('stress')
  })

  it('ignores a non-numeric reading instead of printing it', () => {
    expect(soilMoisture(latest({ soil_moisture_pct: 'mucha' }), null).value).toBe('Sin lectura')
  })
})

describe('forecastRows (Inicio item 4)', () => {
  const today = '2026-09-30'

  it('names today and tomorrow, and the weekday after that', () => {
    const rows = forecastRows([day(today), day('2026-10-01'), day('2026-10-02')], today)

    expect(rows.map((row) => row.label)).toEqual(['Hoy', 'Mañana', 'viernes'])
  })

  it('shows the rain and the temperature range of each day', () => {
    const [row] = forecastRows([day(today)], today)

    expect(row.rain).toBe('12 mm')
    expect(row.temps).toBe('24–31 °C')
  })

  it('uses a bare dash for a value the forecast does not carry', () => {
    // `formatPercent`'s own convention for a missing number, so a missing rain
    // is not "0 mm" and a missing extreme is not a fake 0 °C.
    const [row] = forecastRows([day(today, { rain_mm: null, tmin_c: null, tmax_c: null })], today)

    expect(row.rain).toBe('—')
    expect(row.temps).toBe('—')
  })

  it('shows the one temperature extreme it has, rather than a range with a hole', () => {
    const [row] = forecastRows([day(today, { tmin_c: 24, tmax_c: null })], today)

    expect(row.temps).toBe('24 °C')
  })

  it('carries the stale flag through, so an old forecast says so', () => {
    const [fresh, stale] = forecastRows(
      [day(today, { stale: false }), day('2026-10-01', { stale: true })],
      today,
    )

    expect(fresh.stale).toBe(false)
    expect(stale.stale).toBe(true)
  })

  it('returns nothing at all for a plot with no forecast rows', () => {
    expect(forecastRows([], today)).toEqual([])
  })
})

describe('nodeStateLabel (Inicio item 5)', () => {
  it('names every node status in one plain word (docs/07: never colour alone)', () => {
    // The same four words `features/nodes/containers/PlotNodesSection.tsx` uses.
    expect(nodeStateLabel('online')).toBe('En línea')
    expect(nodeStateLabel('offline')).toBe('Sin señal')
    expect(nodeStateLabel('provisioned')).toBe('Sin vincular')
    expect(nodeStateLabel('retired')).toBe('Retirado')
  })

  it('keeps a status this build does not know instead of guessing one', () => {
    expect(nodeStateLabel('dormido')).toBe('dormido')
  })
})
