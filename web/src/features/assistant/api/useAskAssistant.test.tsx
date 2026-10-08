import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { ASSISTANT_COPY } from './assistantCopy'
import { useAskAssistant } from './useAskAssistant'

const QUESTION = '¿Por qué no regué ayer?'

const DONE_PAYLOAD = {
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  model: 'deepseek-chat',
  prompt_tokens: 120,
  completion_tokens: 45,
  cost_cop: '0.00',
}

const CITATION = {
  source: 'Boletín Agroclimático Nacional',
  title: 'Boletín de octubre de 2026',
  kind: 'agroclimatic_bulletin',
  source_url: 'https://example.com/boletin.pdf',
  published_on: '2026-10-01',
  similarity: 0.87,
}

function tokenFrame(text: string): string {
  return `event: token\ndata: ${JSON.stringify({ text })}\n\n`
}

function sourcesFrame(citations: unknown[]): string {
  return `event: sources\ndata: ${JSON.stringify({ citations })}\n\n`
}

function doneFrame(payload: unknown = DONE_PAYLOAD): string {
  return `event: done\ndata: ${JSON.stringify(payload)}\n\n`
}

/** A stream the test drives by hand, so a frame can arrive before the body closes. */
interface Controllable {
  readonly response: Response
  push(chunk: string): void
  close(): void
}

function controllableStream(): Controllable {
  const encoder = new TextEncoder()
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
    },
  })
  return {
    response: new Response(body, {
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
    }),
    push(chunk) {
      controller?.enqueue(encoder.encode(chunk))
    },
    close() {
      controller?.close()
    },
  }
}

function statusResponse(status: number): Response {
  return new Response('unused', { status, headers: { 'content-type': 'application/json' } })
}

function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

function renderAssistant(plotId: string | null = 'plot-9') {
  return renderHook(() => useAskAssistant(plotId))
}

/** `jsdom`'s `navigator.onLine` is a prototype getter, so it is shadowed per test. */
function setOnline(value: boolean): void {
  Object.defineProperty(navigator, 'onLine', { value, configurable: true })
}

describe('useAskAssistant', () => {
  beforeEach(() => {
    setOnline(true)
    setSession('token-123', 'org-1')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    setOnline(true)
    vi.unstubAllGlobals()
  })

  it('appends the user turn and one streaming assistant turn on send', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    expect(result.current.turns).toEqual([
      { role: 'user', text: QUESTION, citations: [], status: 'complete' },
      { role: 'assistant', text: '', citations: [], status: 'streaming' },
    ])
    expect(result.current.busy).toBe(true)

    stream.push(doneFrame())
    stream.close()
    await waitFor(() => expect(result.current.busy).toBe(false))
  })

  it('streams the tokens into the assistant turn and completes it', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    stream.push(tokenFrame('Hoy '))
    await waitFor(() => expect(result.current.turns[1]?.text).toBe('Hoy '))
    expect(result.current.turns[1]?.status).toBe('streaming')

    stream.push(tokenFrame('no necesita riego.'))
    stream.push(sourcesFrame([CITATION]))
    stream.push(doneFrame())
    stream.close()

    await waitFor(() => expect(result.current.turns[1]?.status).toBe('complete'))
    expect(result.current.turns[1]?.text).toBe('Hoy no necesita riego.')
    expect(result.current.turns[1]?.citations).toEqual([CITATION])
    expect(result.current.busy).toBe(false)
    expect(result.current.notice).toBeNull()
  })

  it('keeps the degraded turn and its citations when the done frame has no model', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    stream.push(tokenFrame('Datos de la parcela "La Esperanza".'))
    stream.push(sourcesFrame([CITATION]))
    stream.push(doneFrame({ ...DONE_PAYLOAD, model: null }))
    stream.close()

    await waitFor(() => expect(result.current.turns[1]?.status).toBe('degraded'))
    expect(result.current.turns[1]?.text).toBe('Datos de la parcela "La Esperanza".')
    expect(result.current.turns[1]?.citations).toEqual([CITATION])
  })

  it('sets the limited notice on 429 and appends no assistant turn', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve(statusResponse(429)))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    await waitFor(() => expect(result.current.notice).toBe('limited'))
    expect(result.current.turns.some((turn) => turn.role === 'assistant')).toBe(false)
    expect(result.current.turns).toEqual([
      { role: 'user', text: QUESTION, citations: [], status: 'complete' },
    ])
    expect(result.current.busy).toBe(false)
  })

  it('sets the offline notice on an offline browser without fetching', async () => {
    setOnline(false)

    const { result } = renderAssistant()
    expect(result.current.online).toBe(false)

    act(() => {
      result.current.send(QUESTION)
    })

    await waitFor(() => expect(result.current.notice).toBe('offline'))
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()

    setOnline(true)
    act(() => {
      window.dispatchEvent(new Event('online'))
    })
    expect(result.current.online).toBe(true)

    setOnline(false)
    act(() => {
      window.dispatchEvent(new Event('offline'))
    })
    expect(result.current.online).toBe(false)
  })

  it('reuses the conversation id the first done frame returned', async () => {
    const first = controllableStream()
    const second = controllableStream()
    vi.mocked(fetch)
      .mockReturnValueOnce(Promise.resolve(first.response))
      .mockReturnValueOnce(Promise.resolve(second.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send('¿Qué alertas tengo?')
    })
    first.push(doneFrame({ ...DONE_PAYLOAD, conversation_id: 'conv-7' }))
    first.close()
    await waitFor(() => expect(result.current.busy).toBe(false))

    act(() => {
      result.current.send('¿Y el riesgo?')
    })
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2))

    const [, init] = vi.mocked(fetch).mock.calls[1]
    expect(JSON.parse(String(init?.body))).toEqual({
      content: '¿Y el riesgo?',
      plot_id: 'plot-9',
      conversation_id: 'conv-7',
    })
  })

  it('does nothing when there is no plot', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve(controllableStream().response))

    const { result } = renderAssistant(null)
    act(() => {
      result.current.send(QUESTION)
    })

    expect(result.current.turns).toEqual([])
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('blocks a concurrent second send while a question is in flight', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send('Primera pregunta')
    })
    act(() => {
      result.current.send('Segunda pregunta')
    })

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    expect(result.current.turns).toHaveLength(2)
    expect(result.current.turns[0]?.text).toBe('Primera pregunta')

    stream.push(doneFrame())
    stream.close()
    await waitFor(() => expect(result.current.busy).toBe(false))
  })

  it('keeps the partial turn as streaming when the stream is interrupted', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    stream.push(tokenFrame('La respuesta '))
    await waitFor(() => expect(result.current.turns[1]?.text).toBe('La respuesta '))
    stream.push('event: error\ndata: {"detail":"no se pudo completar la respuesta del asistente"}\n\n')
    stream.close()

    await waitFor(() => expect(result.current.notice).toBe('failed'))
    expect(result.current.turns[1]?.status).toBe('streaming')
    expect(result.current.turns[1]?.text).toBe('La respuesta ')
    expect(result.current.busy).toBe(false)
  })

  it('drops the empty assistant turn on unavailable, keeping the question and the notice', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve(statusResponse(503)))

    const { result } = renderAssistant()
    act(() => {
      result.current.send(QUESTION)
    })

    await waitFor(() => expect(result.current.notice).toBe('failed'))
    // No token ever arrived, so the turn is an empty bubble still marked
    // "Respondiendo": the question stays with the notice, and nothing claims an
    // answer is on its way.
    expect(result.current.turns).toEqual([
      { role: 'user', text: QUESTION, citations: [], status: 'complete' },
    ])
    expect(result.current.busy).toBe(false)
  })

  it('does not send an empty question', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve(controllableStream().response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send('   ')
    })

    expect(result.current.turns).toEqual([])
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('sends the question trimmed', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))

    const { result } = renderAssistant()
    act(() => {
      result.current.send('  ¿Qué alertas tengo?  ')
    })

    expect(result.current.turns[0]?.text).toBe('¿Qué alertas tengo?')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect(JSON.parse(String(init?.body)).content).toBe('¿Qué alertas tengo?')

    stream.push(doneFrame())
    stream.close()
    await waitFor(() => expect(result.current.busy).toBe(false))
  })

  it('does not send a question over the server bound', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve(controllableStream().response))

    const { result } = renderAssistant()
    act(() => {
      // `router.py:73-86` bounds `content` at 2000 characters, so a longer
      // question is a `422` round trip this only pays to avoid.
      result.current.send(`x${'y'.repeat(2000)}`)
    })

    expect(result.current.turns).toEqual([])
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('starts a new conversation when the active plot changes', async () => {
    const first = controllableStream()
    const second = controllableStream()
    const third = controllableStream()
    vi.mocked(fetch)
      .mockReturnValueOnce(Promise.resolve(first.response))
      .mockReturnValueOnce(Promise.resolve(second.response))
      .mockReturnValueOnce(Promise.resolve(third.response))

    const { result, rerender } = renderHook(
      ({ plotId }: { plotId: string | null }) => useAskAssistant(plotId),
      { initialProps: { plotId: 'plot-1' } },
    )

    act(() => {
      result.current.send('¿Qué alertas tiene la parcela?')
    })
    first.push(doneFrame({ ...DONE_PAYLOAD, conversation_id: 'conv-1' }))
    first.close()
    await waitFor(() => expect(result.current.busy).toBe(false))

    act(() => {
      result.current.send('¿Y el riesgo?')
    })
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2))
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toEqual({
      content: '¿Y el riesgo?',
      plot_id: 'plot-1',
      conversation_id: 'conv-1',
    })

    // The plot switches while the second question is still on the wire.
    rerender({ plotId: 'plot-2' })
    const inFlight = vi.mocked(fetch).mock.calls[1]?.[1]?.signal as AbortSignal | undefined
    expect(inFlight?.aborted).toBe(true)
    // The abandoned stream still answers: its outcome settles the turn, so the
    // hook is free for the next question.
    await waitFor(() => expect(result.current.busy).toBe(false))

    act(() => {
      result.current.send('¿Y en esta otra parcela?')
    })
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3))

    // A conversation is about the plot it was created with (`router.py:_prepare`),
    // so continuing the previous one would answer about the new plot inside the
    // old transcript: it is dropped and the field is simply absent.
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2]?.[1]?.body))).toEqual({
      content: '¿Y en esta otra parcela?',
      plot_id: 'plot-2',
    })
    third.push(doneFrame())
    third.close()
    await waitFor(() => expect(result.current.busy).toBe(false))
  })

  it('aborts on unmount and writes no state afterwards', async () => {
    const stream = controllableStream()
    vi.mocked(fetch).mockReturnValue(Promise.resolve(stream.response))
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    const view = renderAssistant()
    act(() => {
      view.result.current.send(QUESTION)
    })
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1))
    const signal = vi.mocked(fetch).mock.calls[0]?.[1]?.signal as AbortSignal | undefined
    const beforeUnmount = view.result.current

    view.unmount()

    // The in-flight request is cancelled: the abort is what the browser gives back
    // to the server, and it is the assertion that fails if the cleanup is dropped.
    expect(signal?.aborted).toBe(true)

    await act(async () => {
      await tick()
    })

    // The unmount also cancelled the body and released its reader, so a frame the
    // stream could still deliver has nowhere to go: the strongest form of "does not
    // update state afterwards", and it throws because the stream is already closed
    // rather than reaching the hook at all.
    expect(() => stream.push(tokenFrame('tarde'))).toThrow(/Invalid state/)
    expect(stream.response.body?.locked).toBe(false)
    expect(view.result.current).toBe(beforeUnmount)
    expect(consoleError).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })
})

/**
 * The words each of the three notices speaks, pinned.
 *
 * docs/07 §Asistente asks the screen to let a producer tell the states apart
 * "con su texto y no con un código", so the copy is the contract: a failure that
 * borrows the offline words would claim the connection is at fault, and a 503 is a
 * server-side provider gap the producer cannot fix from the phone.
 */
describe('ASSISTANT_COPY', () => {
  it('gives a failed answer the neutral line, never the connection one', () => {
    expect(ASSISTANT_COPY.failedTitle).toBe('No se pudo responder')
    // The repo's own neutral failure line, the same one `lib/api/errorCopy.ts:15`
    // uses for a failure that is not a network one.
    expect(ASSISTANT_COPY.failedBody).toBe('Ocurrió un error. Intenta de nuevo.')
    expect(ASSISTANT_COPY.failedBody).not.toBe('Revisa tu conexión e intenta de nuevo.')
  })

  it('keeps the two lines docs/07 §Asistente writes verbatim', () => {
    expect(ASSISTANT_COPY.limitedTitle).toBe('Límite alcanzado')
    expect(ASSISTANT_COPY.limitedBody).toBe('Mañana podés seguir preguntando.')
    // The connection message belongs to the offline state alone.
    expect(ASSISTANT_COPY.offlineTitle).toBe('Sin conexión')
    expect(ASSISTANT_COPY.offlineBody).toBe('El asistente necesita conexión para responder.')
  })
})
