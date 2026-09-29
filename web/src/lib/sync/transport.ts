import { ApiError, apiClient } from '../api/client'
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

export async function pushChanges(request: PushRequest): Promise<PushResponse> {
  const token = getToken()
  if (token === null) throw new SyncStoppedError('unauthorized', 401)

  try {
    const { data, response } = await apiClient.POST('/api/v1/sync/push', {
      body: request,
    })
    if (!data) throw new SyncStoppedError('unavailable', response?.status ?? null)
    return data
  } catch (error) {
    if (error instanceof SyncStoppedError) throw error
    if (error instanceof ApiError) {
      if (error.status === 401) {
        throw new SyncStoppedError('unauthorized', 401)
      }
      throw new SyncStoppedError('unavailable', error.status)
    }
    // Network errors, timeouts, SyntaxError (non-JSON 2xx):
    throw new SyncStoppedError('unavailable', null)
  }
}

export async function pullChanges(since: number, limit: number): Promise<PullResponse> {
  const token = getToken()
  if (token === null) throw new SyncStoppedError('unauthorized', 401)

  try {
    const { data, response } = await apiClient.GET('/api/v1/sync/pull', {
      params: {
        query: { since, limit },
      },
    })
    if (!data) throw new SyncStoppedError('unavailable', response?.status ?? null)
    return data as PullResponse
  } catch (error) {
    if (error instanceof SyncStoppedError) throw error
    if (error instanceof ApiError) {
      if (error.status === 401) {
        throw new SyncStoppedError('unauthorized', 401)
      }
      throw new SyncStoppedError('unavailable', error.status)
    }
    throw new SyncStoppedError('unavailable', null)
  }
}
