/**
 * Spanish error copy by cause (#21 round 10). A 401 that carried a bearer
 * token is handled globally by `client.ts`'s middleware (session cleared,
 * redirect to sign-in) before it ever reaches a caller here; a 401 from an
 * anonymous call (`/dev/auth/*`, #21 round 11) still reaches this function
 * as an `ApiError`, so this classifies what's left: a network/timeout
 * failure (no connection, DNS, CORS, or the client-side abort from
 * `client.ts`'s request timeout) vs. anything else, including that in-flow
 * anonymous 401.
 */
export function describeApiError(error: unknown): string {
  if (error instanceof TypeError || error instanceof DOMException) {
    return 'Revisa tu conexión e intenta de nuevo.'
  }
  return 'Ocurrió un error. Intenta de nuevo.'
}
