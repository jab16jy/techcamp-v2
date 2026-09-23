import { describe, expect, it } from 'vitest'
import { getSeverityConfig, type Severity } from './severity'

describe('getSeverityConfig', () => {
  it.each<[Severity, string, string]>([
    ['info', 'Información', 'bg-severity-info'],
    ['warning', 'Advertencia', 'bg-severity-warning'],
    ['critical', 'Crítico', 'bg-severity-critical'],
  ])('maps %s to label %s and color class %s', (severity, label, colorClass) => {
    const config = getSeverityConfig(severity)

    expect(config.label).toBe(label)
    expect(config.colorClass).toBe(colorClass)
    expect(config.Icon).toBeTypeOf('function')
  })
})
