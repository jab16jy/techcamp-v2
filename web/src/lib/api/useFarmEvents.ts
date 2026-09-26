import { useEffect, useRef } from 'react'
import { expireSession } from './client'
import { createSseParser } from './sse'
import { getToken } from './session'

export interface FarmEvent {
  id?: string
  event: string
  /** The event's JSON payload, parsed once here; the consumer narrows it. */
  data: unknown
}

const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000

/** Sleeps, but wakes on abort so an unmount never leaves a pending timer behind. */
export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * `GET /api/v1/stream?farm_id=` (docs/04-api.md:180-189, ADR-0015) read with `fetch` +
 * `ReadableStream` instead of `EventSource` (owner decision 2026-09-25): the bearer token
 * rides in the `Authorization` header, as it does for every other call. Reconnects with
 * backoff after a network error or a clean end, sending `Last-Event-ID`; a 401 signs the
 * session out and a 404 (a farm of another org) stops, because neither gets better by
 * retrying.
 */
export function useFarmEvents(farmId: string | null, onEvent: (event: FarmEvent) => void): void {
  // A ref, so an inline callback does not tear the stream down on every render.
  const handlerRef = useRef(onEvent)
  useEffect(() => {
    handlerRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (farmId === null) return
    const farm = farmId
    const controller = new AbortController()
    let stopped = false
    let lastEventId: string | null = null
    let attempt = 0

    async function connect(): Promise<void> {
      while (!stopped) {
        const token = getToken()
        if (token === null) return
        const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
        if (lastEventId !== null) headers['Last-Event-ID'] = lastEventId

        try {
          const response = await fetch(
            `/api/v1/stream?farm_id=${encodeURIComponent(farm)}`,
            { headers, signal: controller.signal },
          )
          if (response.status === 401) {
            expireSession()
            return
          }
          if (response.status === 404) return
          if (!response.ok || response.body === null) throw new Error(`stream: ${response.status}`)

          const parser = createSseParser()
          const decoder = new TextDecoder()
          const reader = response.body.getReader()
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            for (const event of parser.push(decoder.decode(value, { stream: true }))) {
              attempt = 0
              if (event.id !== undefined) lastEventId = event.id
              handlerRef.current({ id: event.id, event: event.event, data: parseData(event.data) })
            }
          }
        } catch {
          if (stopped || controller.signal.aborted) return
        }

        attempt += 1
        await sleep(Math.min(INITIAL_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS), controller.signal)
      }
    }

    void connect()
    return () => {
      stopped = true
      controller.abort()
    }
  }, [farmId])
}

/** The wire sends JSON; a payload that is not JSON is handed over as text rather than
 * thrown away, so a malformed event is visible instead of silently lost. */
function parseData(data: string): unknown {
  try {
    return JSON.parse(data)
  } catch {
    return data
  }
}

export interface ReadingEventData {
  plot_id: string
  metric: string
  value: number
  at: string
}

/** Narrows `data: unknown` to the `reading` payload the server sends (docs/04-api.md:182,
 * reduced by `sse_hub._to_stream_event`). Note what it does NOT carry: `sensor_id` and
 * `depth_cm`, so a live reading cannot be attributed to a depth. */
export function asReadingEvent(data: unknown): ReadingEventData | null {
  if (typeof data !== 'object' || data === null) return null
  const record = data as Record<string, unknown>
  if (typeof record.plot_id !== 'string') return null
  if (typeof record.metric !== 'string') return null
  if (typeof record.value !== 'number') return null
  if (typeof record.at !== 'string') return null
  return { plot_id: record.plot_id, metric: record.metric, value: record.value, at: record.at }
}
