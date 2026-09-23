import { describe, expect, it } from 'vitest'
import { getStatusConfig, type StatusState } from './status'

describe('getStatusConfig', () => {
  it.each<[StatusState, string, string]>([
    ['ok', 'Bien', 'bg-status-ok'],
    ['watch', 'Vigilar', 'bg-status-watch'],
    ['irrigate', 'Regar', 'bg-status-irrigate'],
    ['stress', 'Estrés', 'bg-status-stress'],
  ])('maps %s to label %s and color class %s', (status, label, colorClass) => {
    const config = getStatusConfig(status)

    expect(config.label).toBe(label)
    expect(config.colorClass).toBe(colorClass)
    expect(config.Icon).toBeTypeOf('function')
  })
})
