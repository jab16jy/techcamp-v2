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
  /** null when the answer came from the degraded no-LLM branch (docs/07 "Sin LLM"). */
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
  signal?: AbortSignal
  /** Defaults to `expireSession()`, the 401 path every authenticated call shares. */
  onSessionExpired?: () => void
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
 * - `interrupted` — an `error` frame, a network failure after the first token, or
 *   an `AbortError`; the tokens already delivered stay delivered;
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

  let response: Response
  try {
    response = await fetchImpl(ASSISTANT_ENDPOINT, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: deps.signal,
    })
  } catch (error) {
    // The request itself never reached an answer, so there is nothing to show.
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

  return readAssistantStream(response.body, onToken)
}

/**
 * Reads the frames of one answer.
 *
 * `token` frames are handed to `onToken` as they arrive, so the screen writes
 * while the rest of the answer is still on the wire; `sources` is kept until
 * `done` needs it, and `done` or `error` ends the read — `done` is the terminal
 * frame, `error` arrives with no `done` after it (`stream.py:88-152`).
 */
async function readAssistantStream(
  body: ReadableStream<Uint8Array>,
  onToken: (text: string) => void,
): Promise<AskAssistantOutcome> {
  const tokens: string[] = []
  let citations: AssistantCitation[] = []
  let conversationId: string | null = null
  let messageId: string | null = null
  let model: string | null = null

  const parser = createSseParser()
  const decoder = new TextDecoder()
  const reader = body.getReader()

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
        // `MessageEvidence.no_llm`).
        return model === null ? { status: 'degraded', answer } : { status: 'answered', answer }
      }
      case 'error':
        // Terminal, and partial on purpose: the tokens that arrived are already
        // on screen and the producer has to see that the answer stopped there.
        return { status: 'interrupted' }
      default:
        return null
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        const outcome = onFrame(event)
        if (outcome !== null) return outcome
      }
    }
  } catch (error) {
    if (isAbortError(error)) return { status: 'interrupted' }
    return tokens.length > 0 ? { status: 'interrupted' } : { status: 'unavailable' }
  }

  // A body that ends without a `done` frame is an answer nobody finished: it is
  // not shown as complete, because nothing says it is.
  return tokens.length > 0 ? { status: 'interrupted' } : { status: 'unavailable' }
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
