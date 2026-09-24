import { getToken } from './session'

/**
 * Base URL for API requests. Empty by default: in dev, `vite.config.ts`
 * proxies the known API path prefixes to the `api` service (see
 * `infra/compose.yaml`), and docs/05's production diagram has Caddy proxy
 * the same way, so same-origin relative requests work in both. `VITE_API_URL`
 * overrides it for a standalone/cross-origin setup.
 */
const API_BASE_URL: string = import.meta.env.VITE_API_URL ?? ''

/**
 * Raised for a non-2xx response. `title`/`detail` come from the server's
 * `application/problem+json` body (docs/04-api.md) when the endpoint raised
 * a `ProblemError`. FastAPI's own default `RequestValidationError` handler
 * (`shared/errors.py`) instead returns `{"detail": [{msg, loc, type}, ...]}`
 * with a plain `application/json` content type — not the documented
 * problem+json shape — so this also recognizes that shape (a doc/code gap,
 * see the T7 progress notes).
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

export interface ApiFetchInit extends Omit<RequestInit, 'body'> {
  body?: unknown
}

/** Small fetch wrapper: base URL, bearer token, JSON body/response, problem+json errors. */
export async function apiFetch<T>(path: string, init: ApiFetchInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  const hasBody = init.body !== undefined
  if (hasBody) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    body: hasBody ? JSON.stringify(init.body) : undefined,
  })

  if (!response.ok) {
    const { title, detail } = await parseErrorBody(response)
    throw new ApiError(response.status, title, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
