import { describe, expect, it } from 'vitest'
import { describeApiError } from './errorCopy'

describe('describeApiError', () => {
  it('returns connection copy for a network TypeError', () => {
    expect(describeApiError(new TypeError('Failed to fetch'))).toBe(
      'Revisa tu conexión e intenta de nuevo.',
    )
  })

  it('returns connection copy for a DOMException TimeoutError (client.ts request timeout)', () => {
    expect(describeApiError(new DOMException('The operation timed out.', 'TimeoutError'))).toBe(
      'Revisa tu conexión e intenta de nuevo.',
    )
  })

  it('returns generic copy for anything else, including an ApiError', () => {
    expect(describeApiError(new Error('boom'))).toBe('Ocurrió un error. Intenta de nuevo.')
  })
})
