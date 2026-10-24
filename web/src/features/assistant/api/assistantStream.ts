import { expireSession } from '../../../lib/api/client'
import { createSseParser, type SseEvent } from '../../../lib/api/sse'
import { getToken } from '../../../lib/api/session'

/**
 * `POST /assistant/messages` (docs/04-api.md:227, ADR-0027): the question as the
 * request body, the answer as its own `text/event-stream` body. Relative, like
 * every other call the browser makes (docs/04-api.md, `main.py` mounts the
 * routers under `/api/v1`).
 */
export const ASSISTANT_ENDPOINT = '/api/v1/assistant/messages'

/**
 * How long a question is allowed to go without a single byte before it is given
 * up on.
 *
 * A stream gets a **stall** timeout rather than `client.ts`'s
 * `REQUEST_TIMEOUT_MS`, because an answer arrives token by token and legitimately
 * takes longer than the 10 s a single JSON call gets: this clock measures silence,
 * not total duration, and every chunk that arrives restarts it. Without it, a
 * response that never comes would leave the composer latched on "Respondiendo…"
 * forever and every later question refused as busy.
 *
 * The number itself is a client resilience tuning value, not a product budget:
 * nothing in docs/07 or docs/04 fixes how long a provider may pause between
 * tokens, so this is a judgement call rather than a documented figure.
 */
export const ASSISTANT_STALL_TIMEOUT_MS = 60_000

/**
 * One cited source of an answer, as `stream.py:_citation_view` writes it into the
 * `sources` frame (docs/04-api.md §Riesgo, métricas y asistente).
 *
 * Snake case on purpose: these are the wire's own field names, and a translation
 * layer here would only be a place for a field to be dropped. `similarity` and the
 * optional strings are `null` when there is no evidence of that kind, never a
 * stand-in value (D-T12.1).
 */
export interface AssistantCitation {
  source: string
  title: string | null
  kind: string
  source_url: string | null
  published_on: string | null
  similarity: number | null
}

/**
 * The finished answer: the whole text, its cited sources, and the ids the next
 * question needs to continue the same conversation (docs/04-api.md:227).
 */
export interface AssistantAnswer {
  text: string
  citations: AssistantCitation[]
  conversationId: string | null
  messageId: string | null
  /**
   * null when the answer came from the degraded no-LLM branch (docs/07 "Sin LLM").
   *
   * Anticipatory: today's server always reports the seminar model, so a `done`
   * with no `model` cannot arrive until T6 wires the no-LLM branch — and this is
   * the single place the client would have to learn about it.
   */
  model: string | null
}

/**
 * What one question can answer with, in the vocabulary the screen renders
 * (docs/07 §Asistente). Never a thrown error and never a status code: the four
 * states of the doc, plus the two the HTTP layer can add.
 */
export type AskAssistantOutcome =
  | { status: 'answered'; answer: AssistantAnswer }
  | { status: 'degraded'; answer: AssistantAnswer }
  | { status: 'limited' }
  | { status: 'offline' }
  | { status: 'unauthorized' }
  | { status: 'unavailable' }
  | { status: 'interrupted' }

/** One question. `conversationId`/`plotId` are omitted on the first turn. */
export interface AskAssistantRequest {
  content: string
  conversationId?: string | null
  plotId?: string | null
}

export interface AskAssistantDeps {
  /** Defaults to the global `fetch`. The caller supplies one to serve the stream. */
  fetchImpl?: typeof fetch
  /** Defaults to `() => typeof navigator !== 'undefined' && navigator.onLine`. */
  isOnline?: () => boolean
  /** The caller's own signal, combined with the stall timeout — never replaced. */
  signal?: AbortSignal
  /** Defaults to `expireSession()`, the 401 path every authenticated call shares. */
  onSessionExpired?: () => void
}

/** A signal that fires after a stretch of silence, restarted on every `reset()`. */
interface StallWatch {
  readonly signal: AbortSignal
  /** Restarts the countdown: called for every chunk that arrives. */
  reset: () => void
  /** Stops the countdown for good, whichever way the call ended. */
  stop: () => void
}

/** One signal plus the cleanup that detaches what built it. */
interface CombinedSignals {
  readonly signal: AbortSignal
  /** Detaches the forwarding listeners, so a finished call leaves nothing behind. */
  dispose: () => void
}

/**
 * ORs the caller's signal with the stall watch into one signal, by hand.
 *
 * `AbortSignal.any` (Chrome 116+, Safari 17.4+) would do it in one call, but it has
 * no polyfill on the older field WebViews this repo targets, where it is simply
 * `undefined`. The feature-detected fallback in `client.ts:combineWithTimeout`
 * drops one of the two signals there, which is a trade a JSON call can absorb and
 * a stream cannot: dropping the stall watch means a silent provider leaves the
 * composer busy forever, and dropping the caller's signal means an unmount cannot
 * cancel the request. So both are always kept — one controller that forwards
 * whichever fires, with nothing to feature-detect and no runtime left behind.
 */
function combineAssistantSignals(
  callerSignal: AbortSignal | undefined,
  stall: StallWatch,
): CombinedSignals {
  const controller = new AbortController()
  const detachers: (() => void)[] = []

  const forward = (source: AbortSignal | undefined): void => {
    if (source === undefined) return
    const onAbort = (): void => controller.abort()
    // An already-fired signal never fires again, so forward it right away.
    if (source.aborted) {
      onAbort()
      return
    }
    source.addEventListener('abort', onAbort, { once: true })
    detachers.push(() => source.removeEventListener('abort', onAbort))
  }

  forward(callerSignal)
  forward(stall.signal)

  return {
    signal: controller.signal,
    dispose: () => {
      for (const detach of detachers) detach()
    },
  }
}

/** The stall watch of one call: a plain `setTimeout`, restarted by the reader. */
function createStallWatch(ms: number): StallWatch {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  const restart = (): void => {
    if (timer !== undefined) clearTimeout(timer)
    timer = setTimeout(() => controller.abort(), ms)
  }
  restart()
  return {
    signal: controller.signal,
    reset: restart,
    stop: () => {
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
    },
  }
}

function defaultIsOnline(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine
}

/**
 * Asks one question and streams its answer.
 *
 * `fetch` + `ReadableStream` + `createSseParser`, never `EventSource` (ADR-0027):
 * `EventSource` cannot open a `POST` and cannot send the `Authorization` header.
 * The shape is `useFarmEvents`'s (`lib/api/useFarmEvents.ts:32-107`): one
 * `AbortController`, the bearer token from `getToken()`, and the frames read
 * incrementally so a token reaches the screen while the rest is still coming.
 *
 * Returns instead of throwing, always:
 * - `offline` — checked first, so no request leaves the device at all;
 * - `unavailable` — no token (nothing was sent), or a 404/5xx/body-less answer;
 * - `unauthorized` — a 401, after the session-expiry hook ran;
 * - `limited` — a 429, whose body is never read and which emits no token;
 * - `interrupted` — an `error` frame, a network failure after the first token, an
 *   `AbortError`, or the stall timeout; the tokens already delivered stay delivered;
 * - `answered` / `degraded` — the `done` frame, `degraded` when it carries no
 *   `model` (docs/06 §9, the no-LLM branch).
 */
export async function askAssistant(
  request: AskAssistantRequest,
  onToken: (text: string) => void,
  deps: AskAssistantDeps = {},
): Promise<AskAssistantOutcome> {
  const isOnline = deps.isOnline ?? defaultIsOnline
  if (!isOnline()) return { status: 'offline' }

  const fetchImpl = deps.fetchImpl ?? fetch
  const token = getToken()
  if (token === null) return { status: 'unavailable' }

  // The wire's own names, and only the fields that carry a value: a `null`
  // `conversation_id` would say "continue the conversation called null", which
  // is not what a first question means (docs/04-api.md:227).
  const body: Record<string, unknown> = { content: request.content }
  if (typeof request.conversationId === 'string') body.conversation_id = request.conversationId
  if (typeof request.plotId === 'string') body.plot_id = request.plotId

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }

  const stall = createStallWatch(ASSISTANT_STALL_TIMEOUT_MS)
  const combined = combineAssistantSignals(deps.signal, stall)
  try {
    let response: Response
    try {
      response = await fetchImpl(ASSISTANT_ENDPOINT, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: combined.signal,
      })
    } catch (error) {
      // The request itself never reached an answer, so there is nothing to show.
      // An abort here is the caller's (unmount) or the stall's; both mean the
      // question stopped, which is `interrupted` rather than `unavailable`.
      return isAbortError(error) ? { status: 'interrupted' } : { status: 'unavailable' }
    }

    // Both of these are decided before the first token, and neither body is read:
    // a 401 means the bearer token is already gone (the same path every
    // authenticated call shares, `lib/api/client.ts:111`), and a 429 is the day's cap
    // speaking (docs/07 §Asistente, "Límite alcanzado").
    if (response.status === 401) {
      const onSessionExpired = deps.onSessionExpired ?? expireSession
      onSessionExpired()
      return { status: 'unauthorized' }
    }
    if (response.status === 429) return { status: 'limited' }
    if (!response.ok || response.body === null) return { status: 'unavailable' }

    return await readAssistantStream(response.body, onToken, {
      signal: combined.signal,
      onChunk: stall.reset,
    })
  } finally {
    // Every exit path: a stalled answer, an abandoned question, a finished one.
    stall.stop()
    combined.dispose()
  }
}

/**
 * Reads the frames of one answer.
 *
 * `token` frames are handed to `onToken` as they arrive, so the screen writes
 * while the rest of the answer is still on the wire; the degraded branch sends
 * its whole answer as one `answer` frame, which takes the same path; `sources`
 * is kept until `done` needs it, and `done` or `error` ends the read — `done`
 * is the terminal frame, `error` arrives with no `done` after it
 * (`stream.py:88-152`).
 */
async function readAssistantStream(
  body: ReadableStream<Uint8Array>,
  onToken: (text: string) => void,
  io: { signal: AbortSignal; onChunk: () => void },
): Promise<AskAssistantOutcome> {
  const tokens: string[] = []
  let citations: AssistantCitation[] = []
  let conversationId: string | null = null
  let messageId: string | null = null
  let model: string | null = null

  const parser = createSseParser()
  const decoder = new TextDecoder()
  const reader = body.getReader()

  // Wake a pending `read()` on abort instead of waiting for a body that will never
  // close: this is what turns a cancelled or stalled stream into an outcome rather
  // than a promise nobody awaits.
  const onAbort = (): void => {
    void reader.cancel().catch(() => undefined)
  }
  io.signal.addEventListener('abort', onAbort, { once: true })

  const onFrame = (event: SseEvent): AskAssistantOutcome | null => {
    const payload = parseJson(event.data)
    switch (event.event) {
      case 'token': {
        const text = tokenText(payload)
        if (text === null) return null
        tokens.push(text)
        onToken(text)
        return null
      }
      case 'answer': {
        // The degraded branch's one complete frame (`stream.py`'s
        // `stream_degraded_answer`): the whole answer instead of a token stream,
        // then the usual `sources` and `done`. It arrives as text through the
        // same path a token would, so the screen writes it as soon as it lands
        // instead of settling the turn with an empty bubble.
        const text = tokenText(payload)
        if (text === null) return null
        tokens.push(text)
        onToken(text)
        return null
      }
      case 'sources':
        citations = parseCitations(payload)
        return null
      case 'done': {
        const record = asRecord(payload)
        conversationId = stringField(record, 'conversation_id')
        messageId = stringField(record, 'message_id')
        model = stringField(record, 'model')
        const answer: AssistantAnswer = {
          text: tokens.join(''),
          citations,
          conversationId,
          messageId,
          model,
        }
        // `model` is null exactly on the degraded branch: the LLM was never
        // called, so nothing is reported and nothing is spent (docs/06 §9,
        // `MessageEvidence.no_llm`). Anticipatory for now — the server always
        // reports the seminar model, so T6's no-LLM branch is what makes this
        // reachable on the wire.
        return model === null ? { status: 'degraded', answer } : { status: 'answered', answer }
      }
      case 'error':
        // Terminal, and partial on purpose: the tokens that arrived are already
        // on screen and the producer has to see that the answer stopped there.
        // The frame's `detail` is not carried: it is an operational message with
        // nowhere to go until T6 defines what the client may show from it, so the
        // notice stays docs/07 §Asistente's own line.
        return { status: 'interrupted' }
      default:
        return null
    }
  }

  try {
    // A signal that was already aborted when this function started never fires
    // `abort` again, and a pending `read()` cannot notice it either: the guard
    // after the loop is unreachable while the read hangs, so this is the only
    // cover for the window between the response arriving and the reader existing.
    // Inside the `try`, so the `finally` still releases the reader that was taken.
    if (io.signal.aborted) return { status: 'interrupted' }

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // Every byte restarts the stall countdown.
      io.onChunk()
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        const outcome = onFrame(event)
        if (outcome !== null) return outcome
      }
    }
  } catch (error) {
    if (isAbortError(error)) return { status: 'interrupted' }
    return tokens.length > 0 ? { status: 'interrupted' } : { status: 'unavailable' }
  } finally {
    io.signal.removeEventListener('abort', onAbort)
    releaseReader(reader)
  }

  // A body that ends without a `done` frame is an answer nobody finished: it is
  // not shown as complete, because nothing says it is. An aborted signal is the
  // caller's own cancellation or the stall's silence, and both are a stop rather
  // than a failure — which is what tells "no answer is coming" from "the request
  // never got anywhere" now that not one token has arrived.
  if (io.signal.aborted) return { status: 'interrupted' }
  return tokens.length > 0 ? { status: 'interrupted' } : { status: 'unavailable' }
}

/**
 * Releases the reader and, with it, the body: an early return that leaves the
 * reader locked strands the connection, because nobody ever reads the rest and
 * the browser holds the stream open.
 *
 * `cancel()` comes first so the browser stops holding a body nobody will finish
 * reading, and `releaseLock()` after so the stream is free for the next reader.
 * Both are guarded: a reader that is already released or errored has nothing left
 * to release, and neither call may turn a settled answer into a rejection.
 */
function releaseReader(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    void reader.cancel().catch(() => undefined)
  } catch {
    /* already released or errored */
  }
  try {
    reader.releaseLock()
  } catch {
    /* see above */
  }
}

/**
 * Narrows one `sources` entry to a citation.
 *
 * `stream.py:_citation_view` sends every field of `Citation` with the optional
 * ones honestly `null`, so the only entries worth dropping are the ones that are
 * not an object at all or that carry no `source` — the provenance, which is the
 * one field the domain always fills (`domain/citations.py:60-63`).
 *
 * `kind` is `''` when the wire says `null`: a citation whose `Citation.kind` is
 * unset still names where it comes from, and the UI reads the empty string as
 * "no kind" rather than being handed a fabricated one.
 */
export function parseCitation(value: unknown): AssistantCitation | null {
  const record = asRecord(value)
  if (record === null) return null
  if (typeof record.source !== 'string') return null
  return {
    source: record.source,
    title: typeof record.title === 'string' ? record.title : null,
    kind: typeof record.kind === 'string' ? record.kind : '',
    source_url: typeof record.source_url === 'string' ? record.source_url : null,
    published_on: typeof record.published_on === 'string' ? record.published_on : null,
    similarity: typeof record.similarity === 'number' ? record.similarity : null,
  }
}

/** Every entry of a `sources` frame that is a citation, in the order sent. */
function parseCitations(payload: unknown): AssistantCitation[] {
  const record = asRecord(payload)
  if (record === null) return []
  const entries: unknown = record.citations
  if (!Array.isArray(entries)) return []
  return entries
    .map((entry: unknown) => parseCitation(entry))
    .filter((citation): citation is AssistantCitation => citation !== null)
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data)
  } catch {
    return null
  }
}

/** The `text` of a `token` frame (`stream.py:113`), or null for any other payload. */
function tokenText(payload: unknown): string | null {
  const record = asRecord(payload)
  if (record === null || typeof record.text !== 'string') return null
  return record.text
}

/** A string field of a frame payload, null when it is absent or of another type. */
function stringField(record: Record<string, unknown> | null, field: string): string | null {
  const value = record?.[field]
  return typeof value === 'string' ? value : null
}

/**
 * The abort every browser raises for a cancelled request, whichever class it is
 * (`DOMException` in Chrome, a plain `Error` under some polyfills), so this reads
 * the name instead of the constructor.
 */
function isAbortError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false
  return (error as { name?: unknown }).name === 'AbortError'
}
