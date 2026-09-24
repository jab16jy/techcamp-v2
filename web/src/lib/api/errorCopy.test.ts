import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
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

  it('returns generic copy for anything else, including a plain Error', () => {
    expect(describeApiError(new Error('boom'))).toBe('Ocurrió un error. Intenta de nuevo.')
  })

  it('returns generic copy for a real ApiError (a server-side problem+json failure, not a connection issue)', () => {
    expect(describeApiError(new ApiError(422, 'Validation error', 'name: Field required'))).toBe(
      'Ocurrió un error. Intenta de nuevo.',
    )
  })
})
