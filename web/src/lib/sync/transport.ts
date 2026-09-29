import { ApiError, apiClient } from '../api/client'
import { getToken } from '../api/session'
import type { PullResponse, PushRequest, PushResponse } from './types'

/**
 * Why a run stopped before finishing. This is a third state, not "nothing was
 * synced": the queued changes are still on the phone and the next run after the
 * user is back sends them (D11).
 */
export type SyncStopReason = 'unauthorized' | 'unavailable' | 'unknown_entity'

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

/** docs/04 §Bitácora: exactly one of the two parents, plus the photo's shape. */
export interface PresignPhotoRequest {
  logbook_entry_id?: string
  extension_visit_id?: string
  content_type: string
  bytes: number
}

/**
 * What `POST /attachments:presign` can answer, as the upload queue reads it.
 *
 * `parent_not_synced` is D8's `404` (the entry or visit is not visible yet),
 * `rejected` carries the `422` reason the user is shown, and `not_sent` is
 * everything that left no answer at all — the photo simply stays pending and
 * the next run presigns again. Only an expired session throws, because that
 * stops the whole run (D11) with nothing lost.
 */
export type PresignPhotoOutcome =
  | { status: 'signed'; uploadUrl: string; objectKey: string }
  | { status: 'parent_not_synced' }
  | { status: 'rejected'; reason: string }
  | { status: 'not_sent' }

export async function presignPhoto(
  request: PresignPhotoRequest,
): Promise<PresignPhotoOutcome> {
  const token = getToken()
  if (token === null) throw new SyncStoppedError('unauthorized', 401)

  try {
    const { data, response } = await apiClient.POST('/api/v1/attachments:presign', {
      body: request,
    })
    if (!data) return classifyMissingPresign(response?.status ?? null)
    return { status: 'signed', uploadUrl: data.upload_url, objectKey: data.object_key }
  } catch (error) {
    if (error instanceof SyncStoppedError) throw error
    if (error instanceof ApiError) {
      if (error.status === 401) throw new SyncStoppedError('unauthorized', 401)
      if (error.status === 404) return { status: 'parent_not_synced' }
      // A 4xx is the server refusing this photo for good (D8: bytes too big, a
      // content type it does not take), so the photo is marked failed with the
      // reason instead of being retried forever.
      if (error.status >= 400 && error.status < 500) {
        return { status: 'rejected', reason: error.title }
      }
      return { status: 'not_sent' }
    }
    // Network errors and non-JSON answers.
    return { status: 'not_sent' }
  }
}

function classifyMissingPresign(status: number | null): PresignPhotoOutcome {
  if (status === 404) return { status: 'parent_not_synced' }
  if (status !== null && status >= 400 && status < 500) {
    return { status: 'rejected', reason: `presign failed with status ${status}` }
  }
  return { status: 'not_sent' }
}

/**
 * PUTs the photo to the presigned URL (ADR-0018: the API never carries photo
 * bytes). `Content-Length` is a FORBIDDEN header — the browser derives it from
 * the body — and T6 signs exactly that number, which is why `body` has to be
 * the very Blob whose length was presigned and why nothing here may re-encode
 * or resize it.
 *
 * Any other answer (a signed URL that expired, a length S3 refused, a dropped
 * connection) returns false: the photo stays pending and a later run presigns
 * again. The orphaned object, if the PUT did land, is ADR-0018's cleanup job.
 */
export async function putPhoto(
  uploadUrl: string,
  body: Blob,
  contentType: string,
): Promise<boolean> {
  try {
    const response = await fetch(uploadUrl, {
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      body,
    })
    return response.ok
  } catch {
    return false
  }
}
