/**
 * docs/07 §Asistente — Más → Asistente: the conversation screen, its notices and
 * its state with no plot. The four states a low-literacy producer has to tell
 * apart each get an assertion here, because this is where those words reach a
 * person.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { forgetActivePlotId, setActivePlotId } from '../../../lib/api/activePlot'
import { clearSession, setSession } from '../../../lib/api/session'
import { ASSISTANT_COPY } from '../api/assistantCopy'
import { AssistantScreen } from './AssistantScreen'

const PLOT_ID = 'plot-1'

const CITATION = {
  source: 'Boletín Agroclimático Nacional',
  title: 'Octubre de 2026',
  kind: 'agroclimatic_bulletin',
  source_url: 'https://example.com/boletin.pdf',
  published_on: '2026-10-01',
  similarity: 0.87,
}

const DONE = {
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  model: 'seminar',
  prompt_tokens: 1,
  completion_tokens: 1,
  cost_cop: '0.00',
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/** The stream the test drives by hand, so a frame can arrive before the body closes. */
interface Controllable {
  readonly body: ReadableStream<Uint8Array>
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
    body,
    push: (chunk) => controller?.enqueue(encoder.encode(chunk)),
    close: () => controller?.close(),
  }
}

function renderScreen(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AssistantScreen />
    </QueryClientProvider>,
  )
}

function ask(question: string): void {
  const field = screen.getByLabelText(ASSISTANT_COPY.composerLabel)
  fireEvent.change(field, { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel }))
}

describe('AssistantScreen', () => {
  beforeEach(() => {
    setSession('token', 'org-1')
    setActivePlotId('org-1', PLOT_ID)
  })

  afterEach(() => {
    clearSession()
    forgetActivePlotId('org-1')
    vi.unstubAllGlobals()
  })

  it('invites the first question instead of opening empty', () => {
    renderScreen()

    expect(screen.getByText(/Preguntá sobre tu parcela/)).toBeInTheDocument()
    expect(screen.getByLabelText(ASSISTANT_COPY.composerLabel)).toBeInTheDocument()
  })

  it('streams the answer into the conversation and then cites its source', async () => {
    const stream = controllableStream()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream.body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
      ),
    )
    renderScreen()

    ask('¿Por qué no regué ayer?')
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1))
    expect(screen.getByText('¿Por qué no regué ayer?')).toBeInTheDocument()

    stream.push(frame('token', { text: 'No regaste' }))
    stream.push(frame('token', { text: ' porque llovió.' }))
    await waitFor(() => expect(screen.getByText('No regaste porque llovió.')).toBeInTheDocument())
    // Still streaming, so the screen says so instead of pretending it is done.
    expect(screen.getByText(ASSISTANT_COPY.answering)).toBeInTheDocument()

    stream.push(frame('sources', { citations: [CITATION] }))
    stream.push(frame('done', DONE))
    stream.close()

    await waitFor(() =>
      expect(screen.getByText('Boletín Agroclimático Nacional · Octubre de 2026')).toBeInTheDocument(),
    )
    expect(screen.queryByText(ASSISTANT_COPY.answering)).not.toBeInTheDocument()
  })

  it('marks an answer the server made without the LLM as degraded', async () => {
    const stream = controllableStream()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream.body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
      ),
    )
    renderScreen()

    ask('¿Y el riesgo?')
    // `model: null` is exactly the no-LLM branch: the answer carries the plot's
    // own facts and documents, and nothing was spent (D-T12.1).
    stream.push(frame('token', { text: 'Tus sensores están bien.' }))
    stream.push(frame('done', { ...DONE, model: null }))
    stream.close()

    await waitFor(() => expect(screen.getByText(ASSISTANT_COPY.degradedNote)).toBeInTheDocument())
  })

  it('says the day is done in the producer’s words, never a 429', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('unused', { status: 429 })))
    renderScreen()

    ask('¿Y el riesgo?')

    await waitFor(() => expect(screen.getByText(ASSISTANT_COPY.limitedTitle)).toBeInTheDocument())
    expect(screen.getByText(ASSISTANT_COPY.limitedBody)).toBeInTheDocument()
    expect(screen.queryByText('429')).not.toBeInTheDocument()
  })

  it('keeps the connection promise instead of faking an answer', () => {
    vi.stubGlobal('navigator', { ...window.navigator, onLine: false })
    renderScreen()

    expect(screen.getByText(ASSISTANT_COPY.offlineBody)).toBeInTheDocument()
    expect(screen.getByLabelText(ASSISTANT_COPY.composerLabel)).toBeDisabled()
  })

  it('says so when no plot is remembered, rather than asking about nothing', () => {
    forgetActivePlotId('org-1')
    renderScreen()

    expect(screen.getByText('No hay una parcela activa')).toBeInTheDocument()
    expect(screen.queryByLabelText(ASSISTANT_COPY.composerLabel)).not.toBeInTheDocument()
  })
})