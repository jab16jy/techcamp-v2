import { cn } from '../../../design-system/ui/utils'
import { ASSISTANT_COPY } from '../api/assistantCopy'
import type { AssistantCitation } from '../api/assistantStream'
import type { AssistantTurn } from '../api/useAskAssistant'

export interface MessageListProps {
  turns: AssistantTurn[]
  /** The composer is busy, so the last answer is still arriving. */
  busy: boolean
}

/**
 * The turn list (docs/07 §Asistente): the conversation about the active plot.
 *
 * One column, oldest question at the top, because a producer scrolls back to find
 * what an answer said about irrigation rather than to admire a layout. The
 * producer's own questions are set apart by weight and surface, not by colour
 * alone — the answer has to be findable in direct sun, where the two would
 * otherwise be the same beige rectangle.
 *
 * An answer cites what it is standing on, so its sources sit directly under it
 * (docs/07: "debajo de cada respuesta, las fuentes citadas que el productor puede
 * abrir"). The question never gets one: a producer does not cite themselves.
 */
export function MessageList({ turns, busy }: MessageListProps) {
  if (turns.length === 0) {
    return (
      <p className="px-4 py-8 text-base text-text-muted">
        Preguntá sobre tu parcela, por ejemplo: ¿por qué no regué ayer?
      </p>
    )
  }

  return (
    <ol className="flex flex-col gap-3 px-4 py-4" aria-label="Conversación con el asistente">
      {turns.map((turn, index) => {
        const streaming = turn.role === 'assistant' && busy && turn.status === 'streaming'
        return (
          <li key={`${turn.role}-${index}`} className="flex flex-col">
            <TurnBubble turn={turn} streaming={streaming} />
            {streaming && <p className="text-sm text-text-muted">{ASSISTANT_COPY.answering}</p>}
            {turn.role === 'assistant' && turn.status !== 'streaming' && (
              <Sources citations={turn.citations} />
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** One turn. The producer's questions read as their own words, the answer as ours. */
function TurnBubble({ turn, streaming }: { turn: AssistantTurn; streaming: boolean }) {
  const isProducer = turn.role === 'user'
  return (
    <div className={cn('flex flex-col gap-1', isProducer ? 'items-end' : 'items-start')} data-role={turn.role}>
      <p
        className={cn(
          'max-w-md whitespace-pre-wrap rounded-md px-3 py-2 text-base leading-relaxed',
          isProducer
            ? 'bg-brand text-surface'
            : 'border border-text/15 bg-surface-raised text-text',
        )}
      >
        {turn.text}
        {/* A caret, not a spinner: the screen already says "Respondiendo…" below,
            and a second moving thing would compete with the answer arriving. */}
        {streaming && (
          <span aria-hidden="true" className="ml-0.5 inline-block h-4 w-0.5 bg-text-muted align-text-bottom" />
        )}
      </p>
      {turn.status === 'degraded' && (
        <p className="max-w-md rounded-md bg-surface-raised px-3 py-2 text-sm text-text-muted">
          {ASSISTANT_COPY.degradedNote}
        </p>
      )}
    </div>
  )
}

/**
 * The cited sources of one answer (docs/07 §Asistente).
 *
 * A source is a link only when the server gave a url to open; a citation without
 * one is still named, because an answer that credits nothing is not an answer a
 * producer can act on, and a fake link is worse than plain text.
 */
function Sources({ citations }: { citations: AssistantCitation[] }) {
  if (citations.length === 0) {
    return <p className="text-sm text-text-muted">{ASSISTANT_COPY.noSources}</p>
  }

  return (
    <div className="max-w-full">
      <p className="text-sm font-semibold text-text-muted">{ASSISTANT_COPY.sourcesTitle}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {citations.map((citation, index) => (
          <li key={`${citation.source}-${index}`} className="text-sm">
            {citation.source_url === null ? (
              <span className="text-text-muted">
                {citation.source}
                {citation.title !== null && ` · ${citation.title}`}
              </span>
            ) : (
              <a
                href={citation.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-text underline underline-offset-2 hover:text-brand"
              >
                {citation.source}
                {citation.title !== null && ` · ${citation.title}`}
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

