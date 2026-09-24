import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './schema'
import { clearSession, getToken } from './session'

/**
 * Base URL for API requests. Empty by default: the browser always calls the
 * relative `/api/v1` (docs/04-api.md: "Versionado: por ruta"), and
 * `vite.config.ts`'s dev proxy forwards that one prefix to the `api` service
 * (see `infra/compose.yaml`); docs/05's production diagram has Caddy proxy
 * the same way, so same-origin relative requests work in both. `VITE_API_URL`
 * only retargets the dev proxy (see `vite.config.ts`) — it never changes what
 * the browser itself calls, so there is exactly one place that decides where
 * `/api/v1` actually goes.
 */
const API_BASE_URL: string = import.meta.env.VITE_API_URL ?? ''

/** Requests are aborted after this long, surfacing as a `TypeError`-like abort error. */
const REQUEST_TIMEOUT_MS = 10_000

/**
 * Raised for a non-2xx response. `title`/`detail` come from the server's
 * `application/problem+json` body (docs/04-api.md) when the endpoint raised
 * a `ProblemError`. FastAPI's own default `RequestValidationError` handler
 * (`shared/errors.py`) instead returns `{"detail": [{msg, ...}]}` with a
 * plain `application/json` content type — not the documented problem+json
 * shape — so this also recognizes that shape (a doc/code gap, see the T7
 * progress notes).
 */
export class ApiError extends Error {
  readonly status: number
  readonly title: string
  readonly detail?: string

  constructor(status: number, title: string, detail?: string) {
    super(detail ?? title)
    this.status = status
    this.title = title
    this.detail = detail
  }
}

interface FastApiValidationItem {
  msg?: unknown
}

async function parseErrorBody(response: Response): Promise<{ title: string; detail?: string }> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    return { title: response.statusText || 'Request failed' }
  }
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>
    if (typeof record.title === 'string') {
      return {
        title: record.title,
        detail: typeof record.detail === 'string' ? record.detail : undefined,
      }
    }
    if (typeof record.detail === 'string') {
      return { title: record.detail }
    }
    if (Array.isArray(record.detail)) {
      const messages = (record.detail as FastApiValidationItem[])
        .map((item) => (typeof item.msg === 'string' ? item.msg : null))
        .filter((msg): msg is string => msg !== null)
      return { title: messages.length > 0 ? messages.join('; ') : 'Invalid request' }
    }
  }
  return { title: response.statusText || 'Request failed' }
}

/**
 * Bearer token, request timeout, and error handling in one middleware (a
 * single object so `onResponse` sees exactly the `Request` `onRequest`
 * produced, with no ordering question between separate middlewares).
 *
 * A 401 only signs the session out when the *failing* request itself carried
 * a token: an anonymous call (OTP request/verify, before any session exists)
 * returning 401 is a normal in-flow error the caller handles (wrong code),
 * not an expired session (#21 round 10).
 */
const sessionMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    // Request bodies aren't consumed by re-wrapping like this (verified: a
    // POST's JSON body and headers both survive the copy).
    return new Request(request, { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) })
  },
  async onResponse({ request, response }) {
    if (response.ok) return response
    const hadSession = request.headers.has('Authorization')
    const { title, detail } = await parseErrorBody(response)
    if (response.status === 401 && hadSession) {
      clearSession()
      if (typeof window !== 'undefined') window.location.assign('/ingreso')
    }
    throw new ApiError(response.status, title, detail)
  },
}

/**
 * Generated, type-safe client (docs/05-arquitectura.md: `openapi-typescript`
 * + `openapi-fetch`). `npm run gen:api` regenerates `./schema.d.ts` from the
 * FastAPI app's own OpenAPI schema.
 *
 * `fetch` is a thin indirection to `globalThis.fetch`, looked up fresh on
 * every call, instead of `createClient`'s own default (which captures
 * whatever `globalThis.fetch` is *once*, at module import time — before a
 * test's `vi.stubGlobal('fetch', ...)` ever runs, since `apiClient` is a
 * module-level singleton). No behavior change outside tests.
 */
export const apiClient = createClient<paths>({
  baseUrl: API_BASE_URL,
  fetch: (...args: Parameters<typeof fetch>) => globalThis.fetch(...args),
})
apiClient.use(sessionMiddleware)
