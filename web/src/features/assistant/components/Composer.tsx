import { useId, useState, type FormEvent } from 'react'
import { Button } from '../../../design-system/ui/button'
import { InfoIcon } from '../../../design-system/ui/icons'
import { ASSISTANT_COPY } from '../api/assistantCopy'
import { MAX_QUESTION_CHARS } from '../api/useAskAssistant'

export interface ComposerProps {
  /** An answer is arriving, so the next question would be refused anyway. */
  busy: boolean
  /** The browser's own connectivity (ADR-0005): this screen needs the network. */
  online: boolean
  onSend(content: string): void
}

/**
 * The writing box (docs/07 §Asistente).
 *
 * A single field and one named button, not a keyboard with a paper-plane glyph:
 * the producer has to know what the control does without reading it, so the
 * button says "Preguntar" in the same words the copy uses everywhere else.
 *
 * Three things it refuses before the network is touched, each for its own
 * reason: nothing typed is not a question, one question is already on the wire,
 * and the assistant cannot answer without a connection. Saying so on the field
 * rather than after a failed send is what docs/07 §Asistente asks for from the
 * offline state — "la pantalla lo dice: necesita red y no finge una respuesta".
 */
export function Composer({ busy, online, onSend }: ComposerProps) {
  const [content, setContent] = useState('')
  const fieldId = useId()
  const question = content.trim()
  const tooLong = question.length > MAX_QUESTION_CHARS
  const canSend = question !== '' && !tooLong && !busy && online

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    if (!canSend) return
    onSend(question)
    setContent('')
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-2 border-t border-text/15 bg-surface px-4 py-3"
    >
      {!online && (
        <p className="flex items-start gap-2 text-sm text-text-muted">
          <InfoIcon className="mt-0.5 size-4 shrink-0" />
          {ASSISTANT_COPY.offlineBody}
        </p>
      )}

      <label htmlFor={fieldId} className="sr-only">
        {ASSISTANT_COPY.composerLabel}
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id={fieldId}
          rows={2}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder={ASSISTANT_COPY.askPlaceholder}
          disabled={!online || busy}
          aria-invalid={tooLong}
          className="max-h-32 min-h-12 flex-1 resize-none rounded-md border border-text/20 bg-surface-raised px-3 py-2 text-base leading-relaxed text-text outline-none placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-50"
        />
        {/* The label never changes: a control names its action, and a producer
            looking at a greyed button should still read what it would do. The
            answer above already says "Respondiendo…" — saying it twice makes the
            screen look like two things are happening when one is. */}
        <Button type="submit" disabled={!canSend}>
          {ASSISTANT_COPY.sendLabel}
        </Button>
      </div>

      {tooLong && (
        <p className="text-sm text-severity-critical">
          La pregunta es muy larga. Escribela en menos de {MAX_QUESTION_CHARS} caracteres.
        </p>
      )}
    </form>
  )
}