import { useCallback, useEffect, useRef, useState } from 'react'
import { askAssistant, type AssistantCitation } from './assistantStream'

/**
 * The one thing a producer has to be told when a question was not answered, in the
 * vocabulary docs/07 §Asistente draws: the day's cap, the missing connection, and
 * everything else that is not either of those two.
 */
export type AssistantNotice = 'limited' | 'offline' | 'failed'

/**
 * One turn of the conversation.
 *
 * `status` is what the server's frames say, not a code: `streaming` while `token`
 * frames arrive, `complete` on a `done` that names its model, `degraded` on a
 * `done` that does not (docs/07 §Asistente, "Sin LLM"). A turn the stream never
 * finished keeps `streaming`, so the screen shows what arrived beside the notice
 * that says the answer stopped — there is no fourth word for it, because docs/07
 * draws four states and none of them is a half-answer.
 */
export interface AssistantTurn {
  role: 'user' | 'assistant'
  text: string
  citations: AssistantCitation[]
  status: 'streaming' | 'complete' | 'degraded'
}

export interface UseAskAssistantResult {
  turns: AssistantTurn[]
  busy: boolean
  notice: AssistantNotice | null
  /** The browser's own connectivity, so the screen can disable the composer. */
  online: boolean
  send(content: string): void
}

/** The two statuses a finished turn settles into: a `done` with a model, or without. */
type SettledStatus = 'complete' | 'degraded'

/**
 * The server's own bound on a question (`router.py:73-86`, `MAX_QUESTION_CHARS`).
 *
 * The server is the authority and stays it: this is the one place the client
 * carries the number, so refusing a question that would only come back as a `422`
 * never widens or narrows what the adapter accepts.
 */
export const MAX_QUESTION_CHARS = 2000

function browserIsOnline(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine
}

/** The notice a failed outcome is worth: three states, not seven outcome codes. */
const FAILED_NOTICES: Record<'unauthorized' | 'unavailable' | 'interrupted', AssistantNotice> = {
  // A sign-out, an unavailable endpoint and a broken stream are all "the question
  // got no answer"; docs/07 §Asistente gives them one word, so they share it here.
  unauthorized: 'failed',
  unavailable: 'failed',
  interrupted: 'failed',
}

/** Appends one token to the trailing assistant turn, which is the one streaming. */
function appendToken(turns: AssistantTurn[], text: string): AssistantTurn[] {
  const last = turns[turns.length - 1]
  if (last === undefined || last.role !== 'assistant') return turns
  const next = [...turns]
  next[next.length - 1] = { ...last, text: last.text + text }
  return next
}

/** Settles the trailing assistant turn with the citations its answer cites. */
function settleTurn(
  turns: AssistantTurn[],
  status: SettledStatus,
  citations: AssistantCitation[],
): AssistantTurn[] {
  const last = turns[turns.length - 1]
  if (last === undefined || last.role !== 'assistant') return turns
  const next = [...turns]
  next[next.length - 1] = { ...last, citations, status }
  return next
}

/**
 * Drops the trailing assistant turn, which holds nothing: the question was never
 * answered and an empty bubble would only look like an answer that says nothing.
 * The user's question stays, because docs/07 §Asistente shows the day's "preguntas
 * usadas" beside the "Mañana podés seguir preguntando" notice.
 */
function dropTrailingAssistantTurn(turns: AssistantTurn[]): AssistantTurn[] {
  const last = turns[turns.length - 1]
  if (last === undefined || last.role !== 'assistant') return turns
  return turns.slice(0, -1)
}

/**
 * The assistant screen's state (docs/07 §Asistente, Más → Asistente; ADR-0027).
 *
 * It asks only when the user asks: there is no fetch on mount, because the screen
 * has nothing to say until a question exists. `plotId` is a parameter rather than
 * something resolved here — the caller already knows which plot it is showing, and
 * a hook that re-derived it would be a second source of truth for the active plot.
 *
 * The transcript is memory only: docs/07 §Asistente keeps the assistant out of the
 * PWA cache, so no `persist: true` query key is involved and nothing here touches
 * `queryClient`.
 */
export function useAskAssistant(plotId: string | null): UseAskAssistantResult {
  const [turns, setTurns] = useState<AssistantTurn[]>([])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<AssistantNotice | null>(null)
  const [online, setOnline] = useState(browserIsOnline)

  /** Aborts the in-flight question and silences every state write after it. */
  const aliveRef = useRef(true)
  const controllerRef = useRef<AbortController | null>(null)
  /** A second `send` is refused synchronously, before any state has settled. */
  const busyRef = useRef(false)
  /** The conversation the first `done` opened, so the next turn continues it. */
  const conversationIdRef = useRef<string | null>(null)
  /**
   * The plot on screen right now, readable from a settled answer.
   *
   * The `send` closure only ever sees the plot it was created with, so comparing
   * against it cannot detect a switch that happened while the request was on the
   * wire (docs/07 §Asistente). It is written in the same effect that aborts the
   * in-flight question, never during render.
   */
  const currentPlotIdRef = useRef(plotId)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      controllerRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    const sync = () => setOnline(browserIsOnline())
    window.addEventListener('online', sync)
    window.addEventListener('offline', sync)
    return () => {
      window.removeEventListener('online', sync)
      window.removeEventListener('offline', sync)
    }
  }, [])

  /**
   * A conversation is about the plot it was created with: `_prepare` keeps the
   * conversation's own plot and answers the new `plot_id` inside the old
   * transcript (`router.py:201-225`), so continuing it after a plot switch would
   * mix two subjects in one transcript. The switch therefore stops the question
   * still on the wire and starts a new conversation.
   *
   * Its outcome still lands — an aborted stream answers `interrupted` — which
   * frees the composer rather than leaving it busy. The turn it was writing is
   * dropped without a notice, because the producer asked about a parcel they have
   * already left (docs/07 §Asistente).
   */
  useEffect(() => {
    // Written before the abort, so the answer that is about to settle already sees
    // the plot it was abandoned on.
    currentPlotIdRef.current = plotId
    controllerRef.current?.abort()
    controllerRef.current = null
    conversationIdRef.current = null
  }, [plotId])

  const send = useCallback(
    (content: string) => {
      if (plotId === null) return
      // Trimmed first: a whitespace-only question is no question, and the text
      // that reaches the wire is the text the producer meant to send.
      const question = content.trim()
      if (question === '' || question.length > MAX_QUESTION_CHARS) return
      if (busyRef.current) return

      // The plot this question was asked about. An answer that settles after the
      // producer has moved to another plot was written about the one they left,
      // so it is dropped rather than shown (docs/07 §Asistente).
      const askedPlotId = plotId

      const controller = new AbortController()
      controllerRef.current = controller
      busyRef.current = true
      setBusy(true)
      setNotice(null)

      setTurns((previous) => [
        ...previous,
        { role: 'user', text: question, citations: [], status: 'complete' },
        { role: 'assistant', text: '', citations: [], status: 'streaming' },
      ])

      // How many tokens this question actually streamed, which is what tells a
      // settled turn that carries words from an empty bubble.
      let streamedTokens = 0
      void askAssistant(
        { content: question, conversationId: conversationIdRef.current, plotId },
        (text) => {
          if (!aliveRef.current) return
          streamedTokens += 1
          setTurns((previous) => appendToken(previous, text))
        },
        { signal: controller.signal },
      )
        .then((outcome) => {
          busyRef.current = false
          setBusy(false)
          if (!aliveRef.current) return
          controllerRef.current = null

          switch (outcome.status) {
            case 'answered':
              conversationIdRef.current = outcome.answer.conversationId
              setTurns((previous) => settleTurn(previous, 'complete', outcome.answer.citations))
              return
            case 'degraded':
              conversationIdRef.current = outcome.answer.conversationId
              setTurns((previous) => settleTurn(previous, 'degraded', outcome.answer.citations))
              return
            case 'limited':
              setNotice('limited')
              setTurns(dropTrailingAssistantTurn)
              return
            case 'offline':
              setNotice('offline')
              setTurns(dropTrailingAssistantTurn)
              return
            case 'unavailable':
            case 'unauthorized':
              setNotice('failed')
              // These two are decided before the stream starts, so `interrupted`
              // and no-token-arrived are the same case today: the turn is an
              // empty bubble marked "Respondiendo" (docs/07 §Asistente), which
              // claims an answer is on its way when none is. The question stays
              // with the notice. A partial answer stays, like `interrupted`.
              if (streamedTokens === 0) setTurns(dropTrailingAssistantTurn)
              return
            default:
              // The producer left the plot this answer was written about, so it is
              // discarded with no notice at all: half an answer about the previous
              // parcel beside a failure message would be worse than nothing, and
              // the composer is already free (docs/07 §Asistente).
              if (currentPlotIdRef.current !== askedPlotId) {
                setTurns(dropTrailingAssistantTurn)
                return
              }
              // Otherwise `interrupted` keeps the turn as `streaming`, with whatever
              // text arrived, so the producer sees the half answer beside the notice.
              setNotice(FAILED_NOTICES[outcome.status])
              return
          }
        })
        .catch(() => {
          // `askAssistant` is written not to throw; this is the last resort, so a
          // question never leaves the composer disabled for the rest of the visit.
          busyRef.current = false
          setBusy(false)
          if (!aliveRef.current) return
          setNotice('failed')
        })
    },
    [plotId],
  )

  return { turns, busy, notice, online, send }
}
