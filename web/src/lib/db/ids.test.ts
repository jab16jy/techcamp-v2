import { afterEach, describe, expect, it, vi } from 'vitest'
import { uuidv7 } from './ids'

const CREATED_AT = '2026-09-28T12:00:00.000Z'

describe('uuidv7', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('is a version 7, variant 10 UUID carrying the 48-bit creation time, so ids sort by age', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(CREATED_AT))
    const earlier = uuidv7()
    vi.setSystemTime(new Date(Date.parse(CREATED_AT) + 1))
    const later = uuidv7()

    // RFC 9562 §5.7: 48-bit big-endian Unix ms first, then version 0111 and variant 10xx.
    expect(earlier.replace(/-/g, '').slice(0, 12)).toBe(
      Date.parse(CREATED_AT).toString(16).padStart(12, '0'),
    )
    expect(earlier[14]).toBe('7')
    expect(['8', '9', 'a', 'b']).toContain(earlier[19])
    // Not a v4 layout: a random v4 has no timestamp prefix and no ordering.
    expect(earlier[14]).not.toBe('4')
    expect(earlier).not.toBe(later)
    expect(earlier < later).toBe(true)
    expect(earlier > later).toBe(false)
  })
})
