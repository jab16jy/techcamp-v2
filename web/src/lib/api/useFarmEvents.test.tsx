import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, getToken, setSession } from './session'
import { asReadingEvent, useFarmEvents, type FarmEvent } from './useFarmEvents'

const READING_FRAME =
  'id: 7\nevent: reading\ndata: {"plot_id":"plot-1","metric":"soil_moisture","value":42.5,"at":"2026-09-25T10:00:00Z"}\n\n'

/** A response whose body is the given text chunks, then a clean end. */
function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

/** Parks the reconnect loop: a fetch that never settles is an open stream. */
function neverEnding(): Promise<Response> {
  return new Promise(() => {})
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function requestHeaders(input: Request | string | URL, init?: RequestInit): Headers {
  if (input instanceof Request && init === undefined) return input.headers
  return new Headers(init?.headers)
}

function renderEvents(farmId: string | null = 'farm-1') {
  const events: FarmEvent[] = []
  const view = renderHook(() => useFarmEvents(farmId, (event) => events.push(event)))
  return { events, view }
}

describe('useFarmEvents', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-123', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('delivers a reading event with its id and parsed data', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(streamResponse([READING_FRAME]))
      .mockReturnValue(neverEnding())

    const { events } = renderEvents()

    await waitFor(() => expect(events).toHaveLength(1))
    expect(events[0]).toEqual({
      id: '7',
      event: 'reading',
      data: { plot_id: 'plot-1', metric: 'soil_moisture', value: 42.5, at: '2026-09-25T10:00:00Z' },
    })
  })

  it('authenticates with the bearer token, like every other request', async () => {
    vi.mocked(fetch).mockReturnValue(neverEnding())

    renderEvents()

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1))
    const [input, init] = vi.mocked(fetch).mock.calls[0]
    expect(requestHeaders(input, init).get('Authorization')).toBe('Bearer token-123')
    expect(requestUrl(input)).toContain('farm_id=farm-1')
  })

  it('reconnects after a clean end, sending Last-Event-ID', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(streamResponse([READING_FRAME]))
      .mockReturnValue(neverEnding())

    renderEvents()

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2), { timeout: 3000 })
    const [input, init] = vi.mocked(fetch).mock.calls[1]
    expect(requestHeaders(input, init).get('Last-Event-ID')).toBe('7')
  }, 5000)

  it('aborts the request on unmount', async () => {
    vi.mocked(fetch).mockReturnValue(neverEnding())

    const { view } = renderEvents()
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1))
    const [, init] = vi.mocked(fetch).mock.calls[0]
    const signal = init?.signal as AbortSignal

    view.unmount()

    expect(signal.aborted).toBe(true)
  })

  it('signs the session out on 401 instead of retrying forever', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ title: 'Not authenticated' }, 401))

    renderEvents()

    await waitFor(() => expect(getToken()).toBeNull())
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  }, 5000)

  it('stops on 404 (a farm of another org) without retrying', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ title: 'Farm not found' }, 404))

    renderEvents()

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1))
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    expect(getToken()).toBe('token-123')
  }, 5000)

  it('does not connect without a farm', () => {
    vi.mocked(fetch).mockReturnValue(neverEnding())

    renderEvents(null)

    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })
})

describe('asReadingEvent', () => {
  const READING = { plot_id: 'plot-1', metric: 'soil_moisture', value: 42.5, at: '2026-09-25T10:00:00Z' }

  it('narrows a reading payload', () => {
    expect(asReadingEvent(READING)).toEqual(READING)
  })

  it('rejects a payload that is not the documented reading shape', () => {
    expect(asReadingEvent(null)).toBeNull()
    expect(asReadingEvent('plot-1')).toBeNull()
    expect(asReadingEvent({ plot_id: 'plot-1' })).toBeNull()
    expect(asReadingEvent({ ...READING, value: '42.5' })).toBeNull()
  })
})
