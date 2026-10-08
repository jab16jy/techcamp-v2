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

  const send = useCallback(
    (content: string) => {
      if (plotId === null) return
      if (busyRef.current) return

      const controller = new AbortController()
      controllerRef.current = controller
      busyRef.current = true
      setBusy(true)
      setNotice(null)

      setTurns((previous) => [
        ...previous,
        { role: 'user', text: content, citations: [], status: 'complete' },
        { role: 'assistant', text: '', citations: [], status: 'streaming' },
      ])

      void askAssistant(
        { content, conversationId: conversationIdRef.current, plotId },
        (text) => {
          if (!aliveRef.current) return
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
            default:
              // `interrupted` keeps the turn as `streaming`, with whatever text
              // arrived, so the producer sees the half answer beside the notice.
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
