import { API_BASE_URL, expireSession, REQUEST_TIMEOUT_MS } from '../api/client'
import { getToken } from '../api/session'
import type { PullResponse, PushRequest, PushResponse } from './types'

/**
 * Why a run stopped before finishing. This is a third state, not "nothing was
 * synced": the queued changes are still on the phone and the next run after the
 * user is back sends them (D11).
 */
export type SyncStopReason = 'unauthorized' | 'unavailable'

export class SyncStoppedError extends Error {
  readonly reason: SyncStopReason
  /** The HTTP status, or null when the request never got an answer. */
  readonly status: number | null

  constructor(reason: SyncStopReason, status: number | null) {
    super(`sync stopped: ${reason}`)
    this.reason = reason
    this.status = status
  }
}

const SYNC_URL = `${API_BASE_URL}/api/v1/sync`

/**
 * One `fetch` for both sync endpoints, carrying the same bearer token and the
 * same timeout as every other authenticated call, and signing the session out on
 * a 401 exactly like `apiClient` does. Kept in its own small file so T8 can
 * delete it in favour of the typed client once the schema has the paths.
 */
async function syncRequest(path: string, init: RequestInit): Promise<Response> {
  const token = getToken()
  // Signed out: there is no session to authenticate, and asking anyway would
  // earn a 401 that signs out a session that is already gone.
  if (token === null) throw new SyncStoppedError('unauthorized', 401)

  let response: Response
  try {
    response = await fetch(`${SYNC_URL}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    })
  } catch {
    // No signal, DNS failure, timeout: none of them is the server's answer, and
    // the queued changes must stay exactly as they are (D11).
    throw new SyncStoppedError('unavailable', null)
  }

  if (response.status === 401) {
    // D11: the seminar profile has no refresh endpoint, so an expired token is
    // gone for good until the user signs in again. Signing out never clears the
    // local logbook, and the outbox is left untouched for the next run.
    expireSession()
    throw new SyncStoppedError('unauthorized', 401)
  }
  if (!response.ok) throw new SyncStoppedError('unavailable', response.status)
  return response
}

export async function pushChanges(request: PushRequest): Promise<PushResponse> {
  const response = await syncRequest('/push', { method: 'POST', body: JSON.stringify(request) })
  return (await response.json()) as PushResponse
}

export async function pullChanges(since: number, limit: number): Promise<PullResponse> {
  const response = await syncRequest(`/pull?since=${since}&limit=${limit}`, { method: 'GET' })
  return (await response.json()) as PullResponse
}
