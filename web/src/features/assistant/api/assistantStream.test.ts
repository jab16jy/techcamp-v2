import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import {
  ASSISTANT_ENDPOINT,
  ASSISTANT_STALL_TIMEOUT_MS,
  askAssistant,
  parseCitation,
  type AskAssistantDeps,
} from './assistantStream'

const QUESTION = '¿Por qué no regué ayer?'

const CITATION = {
  source: 'Boletín Agroclimático Nacional',
  title: 'Boletín de octubre de 2026',
  kind: 'agroclimatic_bulletin',
  source_url: 'https://example.com/boletin.pdf',
  published_on: '2026-10-01',
  similarity: 0.87,
}

const DONE_PAYLOAD = {
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  model: 'deepseek-chat',
  prompt_tokens: 120,
  completion_tokens: 45,
  cost_cop: '0.00',
}

/** One `token` frame, as `stream.py:_frame` writes it: an event name and a JSON `data`. */
function tokenFrame(text: string): string {
  return `event: token\ndata: ${JSON.stringify({ text })}\n\n`
}

function sourcesFrame(citations: unknown[]): string {
  return `event: sources\ndata: ${JSON.stringify({ citations })}\n\n`
}

function doneFrame(payload: unknown = DONE_PAYLOAD): string {
  return `event: done\ndata: ${JSON.stringify(payload)}\n\n`
}

function errorFrame(detail = 'no se pudo completar la respuesta del asistente'): string {
  return `event: error\ndata: ${JSON.stringify({ detail })}\n\n`
}

/** A stream the test drives by hand, so a frame can arrive before the body closes. */
interface Controllable {
  readonly response: Response
  push(chunk: string): void
  close(): void
  fail(error?: unknown): void
}

function controllableStream(status = 200): Controllable {
  const encoder = new TextEncoder()
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
    },
  })
  return {
    response: new Response(body, {
      status,
      headers: { 'content-type': 'text/event-stream' },
    }),
    push(chunk) {
      controller?.enqueue(encoder.encode(chunk))
    },
    close() {
      controller?.close()
    },
    fail(error = new Error('connection lost')) {
      controller?.error(error)
    },
  }
}

function statusResponse(status: number, body: string | null = 'unused'): Response {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } })
}

/** A macrotask tick: every pending microtask (the read loop included) has run by then. */
function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

/** Watches a promise so a test can assert it is still unsettled mid-stream. */
function track(promise: Promise<unknown>): () => boolean {
  let settled = false
  void promise.then(
    () => {
      settled = true
    },
    () => {
      settled = true
    },
  )
  return () => settled
}

function deps(fetchImpl: typeof fetch, extra: AskAssistantDeps = {}): AskAssistantDeps {
  return { fetchImpl, ...extra }
}

/**
 * A `fetch` that never answers, rejecting with an `AbortError` when the request's
 * own signal fires — which is what a real `fetch` does, and the only way a stall
 * timeout applied to the request becomes observable from here.
 */
function fetchThatHangs(): typeof fetch {
  return (_input, init) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      })
    })
}

describe('askAssistant', () => {
  beforeEach(() => {
    setSession('token-123', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('emits each token frame through onToken while the stream is still open', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const tokens: string[] = []
    const promise = askAssistant({ content: QUESTION }, (text) => tokens.push(text), deps(fetchImpl))
    const settled = track(promise)

    stream.push(tokenFrame('Hola'))
    await tick()

    // The answer is on screen before the body ends: nothing here has closed the stream.
    expect(tokens).toEqual(['Hola'])
    expect(settled()).toBe(false)

    stream.push(tokenFrame(' productor'))
    await tick()
    expect(tokens).toEqual(['Hola', ' productor'])
    expect(settled()).toBe(false)

    stream.push(doneFrame())
    stream.close()
    await expect(promise).resolves.toEqual({ status: 'answered', answer: expect.anything() })
  })

  it('concatenates the tokens in order into the answer text', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(tokenFrame('Hoy '))
    stream.push(tokenFrame('no '))
    stream.push(tokenFrame('necesita riego.'))
    stream.push(sourcesFrame([CITATION]))
    stream.push(doneFrame())
    stream.close()

    await expect(promise).resolves.toEqual({
      status: 'answered',
      answer: {
        text: 'Hoy no necesita riego.',
        citations: [CITATION],
        conversationId: 'conv-1',
        messageId: 'msg-1',
        model: 'deepseek-chat',
      },
    })
  })

  it('reads the sources frame into every citation field', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(tokenFrame('respuesta'))
    stream.push(sourcesFrame([CITATION, { source: 'Fuente sin más campos', kind: 'guide' }]))
    stream.push(doneFrame())
    stream.close()

    const outcome = await promise
    expect(outcome.status).toBe('answered')
    if (outcome.status !== 'answered') return
    expect(outcome.answer.citations).toEqual([
      CITATION,
      {
        source: 'Fuente sin más campos',
        title: null,
        kind: 'guide',
        source_url: null,
        published_on: null,
        similarity: null,
      },
    ])
  })

  it('drops a sources entry that is not an object, through parseCitation', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(sourcesFrame(['no soy un objeto', 42, null, { title: 'sin source' }, CITATION]))
    stream.push(doneFrame())
    stream.close()

    const outcome = await promise
    expect(outcome.status).toBe('answered')
    if (outcome.status !== 'answered') return
    expect(outcome.answer.citations).toEqual([CITATION])
  })

  it('settles a turn on the done frame with its conversation, message and model', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(tokenFrame('respuesta'))
    stream.push(doneFrame())
    // `done` is terminal: the body closing afterwards changes nothing.
    stream.close()

    await expect(promise).resolves.toEqual({
      status: 'answered',
      answer: {
        text: 'respuesta',
        citations: [],
        conversationId: 'conv-1',
        messageId: 'msg-1',
        model: 'deepseek-chat',
      },
    })
  })

  it('answers degraded when the done frame carries no model', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(tokenFrame('Datos de la parcela "La Esperanza".'))
    stream.push(sourcesFrame([CITATION]))
    stream.push(doneFrame({ ...DONE_PAYLOAD, model: null }))
    stream.close()

    await expect(promise).resolves.toEqual({
      status: 'degraded',
      answer: {
        text: 'Datos de la parcela "La Esperanza".',
        citations: [CITATION],
        conversationId: 'conv-1',
        messageId: 'msg-1',
        model: null,
      },
    })
  })

  it('answers limited on 429 without reading the body', async () => {
    const stream = controllableStream(429)
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const response = stream.response
    const getReader = vi.spyOn(response.body as ReadableStream<Uint8Array>, 'getReader')
    const onToken = vi.fn()

    await expect(
      askAssistant({ content: QUESTION }, onToken, deps(fetchImpl)),
    ).resolves.toEqual({ status: 'limited' })

    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(getReader).not.toHaveBeenCalled()
    expect(onToken).not.toHaveBeenCalled()
  })

  it('signs the session out through the hook and answers unauthorized on 401', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(statusResponse(401)))
    const onSessionExpired = vi.fn()

    await expect(
      askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl, { onSessionExpired })),
    ).resolves.toEqual({ status: 'unauthorized' })

    expect(onSessionExpired).toHaveBeenCalledTimes(1)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('answers unavailable on 404', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(statusResponse(404)))

    await expect(askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))).resolves.toEqual({
      status: 'unavailable',
    })
  })

  it('answers offline without calling fetch', async () => {
    const fetchImpl = vi.fn<typeof fetch>()

    await expect(
      askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl, { isOnline: () => false })),
    ).resolves.toEqual({ status: 'offline' })

    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('answers unavailable without fetching when there is no token', async () => {
    clearSession()
    const fetchImpl = vi.fn<typeof fetch>()

    await expect(askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))).resolves.toEqual({
      status: 'unavailable',
    })

    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('parses a frame split across two chunks, feeding the parser incrementally', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const tokens: string[] = []
    const promise = askAssistant({ content: QUESTION }, (text) => tokens.push(text), deps(fetchImpl))

    stream.push('event: token\ndata: {"text":"hol')
    await tick()
    expect(tokens).toEqual([])

    stream.push('a"}\n\n')
    await tick()
    expect(tokens).toEqual(['hola'])

    stream.push(doneFrame())
    stream.close()
    await expect(promise).resolves.toMatchObject({ status: 'answered' })
  })

  it('answers interrupted on an error frame, keeping the tokens already delivered', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const tokens: string[] = []
    const promise = askAssistant({ content: QUESTION }, (text) => tokens.push(text), deps(fetchImpl))

    stream.push(tokenFrame('La respuesta '))
    stream.push(tokenFrame('se cortó'))
    stream.push(errorFrame())
    // An `error` frame is terminal: no `done` follows it, not even after the close.
    stream.close()

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
    expect(tokens).toEqual(['La respuesta ', 'se cortó'])
  })

  it('answers interrupted on a network failure after tokens and unavailable before them', async () => {
    const afterTokens = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(afterTokens.response))
    const tokens: string[] = []
    const promise = askAssistant(
      { content: QUESTION },
      (text) => tokens.push(text),
      deps(fetchImpl),
    )

    afterTokens.push(tokenFrame('medio '))
    afterTokens.push(tokenFrame('token'))
    await tick()
    afterTokens.fail()

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
    expect(tokens).toEqual(['medio ', 'token'])

    const beforeTokens = controllableStream()
    const failing = vi.fn<typeof fetch>(() => Promise.resolve(beforeTokens.response))
    const second = askAssistant({ content: QUESTION }, () => {}, deps(failing))
    await tick()
    beforeTokens.fail()

    await expect(second).resolves.toEqual({ status: 'unavailable' })
  })

  it('answers interrupted on an AbortError without throwing', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))
    await tick()

    stream.fail(new DOMException('The operation was aborted.', 'AbortError'))

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
  })

  it('answers interrupted when the body ends cleanly without a done frame', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    stream.push(tokenFrame('respuesta a medias'))
    stream.close()

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
  })

  it('answers unavailable on a 5xx and on a body-less 200', async () => {
    const serverError = vi.fn<typeof fetch>(() => Promise.resolve(statusResponse(503)))
    await expect(askAssistant({ content: QUESTION }, () => {}, deps(serverError))).resolves.toEqual({
      status: 'unavailable',
    })

    const noBody = vi.fn<typeof fetch>(() => Promise.resolve(statusResponse(200, null)))
    await expect(askAssistant({ content: QUESTION }, () => {}, deps(noBody))).resolves.toEqual({
      status: 'unavailable',
    })
  })

  it('posts the question to the assistant endpoint with the bearer token', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    stream.push(doneFrame())
    stream.close()

    await askAssistant(
      { content: QUESTION, conversationId: null, plotId: 'plot-9' },
      () => {},
      deps(fetchImpl),
    )

    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [input, init] = fetchImpl.mock.calls[0]
    expect(String(input)).toBe(ASSISTANT_ENDPOINT)
    const headers = input instanceof Request ? input.headers : new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer token-123')
    expect(JSON.parse(String(init?.body))).toEqual({
      content: QUESTION,
      plot_id: 'plot-9',
    })
  })

  it('releases the reader on a finished answer, so the body is never left locked', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const body = stream.response.body as ReadableStream<Uint8Array>

    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))
    stream.push(tokenFrame('respuesta'))
    stream.push(doneFrame())
    stream.close()

    await expect(promise).resolves.toMatchObject({ status: 'answered' })
    // A reader still locked here would make a second getReader() throw.
    expect(() => body.getReader()).not.toThrow()
  })

  it('releases the reader when the stream is cancelled mid-answer', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const body = stream.response.body as ReadableStream<Uint8Array>
    const controller = new AbortController()

    const promise = askAssistant(
      { content: QUESTION },
      () => {},
      deps(fetchImpl, { signal: controller.signal }),
    )
    await tick()
    stream.push(tokenFrame('a medio'))
    await tick()
    controller.abort()

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
    expect(() => body.getReader()).not.toThrow()
  })

  it('cancels the body an error frame left unread', async () => {
    const stream = controllableStream()
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchImpl))

    // The `error` frame is terminal and the connection stays open behind it, so
    // the client has to let go of the body it will never finish reading.
    stream.push(tokenFrame('a medio '))
    stream.push(errorFrame())
    stream.push(tokenFrame('nunca entregado'))

    await expect(promise).resolves.toEqual({ status: 'interrupted' })
    expect(() => stream.push(tokenFrame('demasiado tarde'))).toThrow(/Invalid state/)
  })

  it('answers interrupted when the response never sends a byte', async () => {
    vi.useFakeTimers()
    try {
      const promise = askAssistant({ content: QUESTION }, () => {}, deps(fetchThatHangs()))

      // Nothing has arrived, and yet the call must not hang forever: the stall
      // timeout fires on silence rather than on a total duration.
      await vi.advanceTimersByTimeAsync(ASSISTANT_STALL_TIMEOUT_MS + 1)

      await expect(promise).resolves.toEqual({ status: 'interrupted' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('answers interrupted when the body stalls mid-stream after tokens', async () => {
    vi.useFakeTimers()
    try {
      const stream = controllableStream()
      const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
      const tokens: string[] = []
      const promise = askAssistant(
        { content: QUESTION },
        (text) => tokens.push(text),
        deps(fetchImpl),
      )

      stream.push(tokenFrame('a medio '))
      // The chunks that did arrive keep the answer alive: the countdown restarts
      // from the last byte, so the answer may legitimately take longer than a
      // plain request.
      await vi.advanceTimersByTimeAsync(1)
      expect(tokens).toEqual(['a medio '])

      await vi.advanceTimersByTimeAsync(ASSISTANT_STALL_TIMEOUT_MS + 1)

      await expect(promise).resolves.toEqual({ status: 'interrupted' })
      expect(tokens).toEqual(['a medio '])
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('parseCitation', () => {
  it('narrows a documented citation', () => {
    expect(parseCitation(CITATION)).toEqual(CITATION)
  })

  it('drops an entry that is not an object', () => {
    expect(parseCitation(null)).toBeNull()
    expect(parseCitation('Boletín Agroclimático Nacional')).toBeNull()
    expect(parseCitation(42)).toBeNull()
    expect(parseCitation([CITATION])).toBeNull()
  })

  it('drops an entry with no source, which is the field that is always there', () => {
    expect(parseCitation({ title: 'Un título sin fuente' })).toBeNull()
    expect(parseCitation({ source: 42 })).toBeNull()
  })

  it('keeps a citation whose optional evidence is missing, honestly null', () => {
    expect(parseCitation({ source: 'ICA', kind: null, similarity: null })).toEqual({
      source: 'ICA',
      title: null,
      kind: '',
      source_url: null,
      published_on: null,
      similarity: null,
    })
  })
})
