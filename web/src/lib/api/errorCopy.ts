/**
 * Spanish error copy by cause (#21 round 10). A 401 on an authenticated call
 * is handled globally by `client.ts`'s middleware (session cleared, redirect
 * to sign-in) before it ever reaches a caller here, so this only classifies
 * what's left: a network/timeout failure (no connection, DNS, CORS, or the
 * client-side abort from `client.ts`'s request timeout) vs. anything else.
 */
export function describeApiError(error: unknown): string {
  if (error instanceof TypeError || error instanceof DOMException) {
    return 'Revisa tu conexión e intenta de nuevo.'
  }
  return 'Ocurrió un error. Intenta de nuevo.'
}
