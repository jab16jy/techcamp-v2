/**
 * docs/07 §Asistente: a conversation screen with "un cuadro de escritura". The
 * producer types in it with one thumb, in the sun, so what matters here is that
 * the control is reachable, labelled in plain words, and honest when it cannot
 * be used.
 *
 * `fireEvent` rather than `@testing-library/user-event`: the repo does not depend
 * on it, and adding a dependency to test a controlled textarea is a worse trade
 * than driving the events the component actually listens to.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ASSISTANT_COPY } from './assistantCopy'
import { MAX_QUESTION_CHARS } from '../api/useAskAssistant'
import { Composer } from './Composer'

function type(field: HTMLElement, value: string): void {
  fireEvent.change(field, { target: { value } })
}

describe('Composer', () => {
  it('labels the field with the words docs/07 asks for', () => {
    render(<Composer busy={false} online onSend={vi.fn()} />)

    expect(screen.getByLabelText(ASSISTANT_COPY.composerLabel)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(ASSISTANT_COPY.askPlaceholder)).toBeInTheDocument()
  })

  it('names the action on the button instead of sending an icon', () => {
    render(<Composer busy={false} online onSend={vi.fn()} />)

    type(screen.getByLabelText(ASSISTANT_COPY.composerLabel), '¿Por qué no regué ayer?')
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel }))
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeDisabled()
  })

  it('sends the typed question and empties the field', () => {
    const onSend = vi.fn()
    render(<Composer busy={false} online onSend={onSend} />)

    const field = screen.getByLabelText(ASSISTANT_COPY.composerLabel)
    type(field, '¿Por qué no regué ayer?')
    fireEvent.click(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel }))

    expect(onSend).toHaveBeenCalledWith('¿Por qué no regué ayer?')
    // Cleared, so the next question does not have to be typed over the last one.
    expect(field).toHaveValue('')
  })

  it('cannot send nothing', () => {
    const onSend = vi.fn()
    render(<Composer busy={false} online onSend={onSend} />)

    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeDisabled()
    type(screen.getByLabelText(ASSISTANT_COPY.composerLabel), '   ')
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel }))
    expect(onSend).not.toHaveBeenCalled()
  })

  it('locks the field while an answer is still arriving', () => {
    render(<Composer busy online onSend={vi.fn()} />)

    expect(screen.getByLabelText(ASSISTANT_COPY.composerLabel)).toBeDisabled()
    // The label stays: a control names its action, and "Respondiendo…" belongs to
    // the answer above, not to a second copy of it here.
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeDisabled()
  })

  it('says the assistant needs a connection instead of failing on send', () => {
    const onSend = vi.fn()
    render(<Composer busy={false} online={false} onSend={onSend} />)

    expect(screen.getByText(ASSISTANT_COPY.offlineBody)).toBeInTheDocument()
    expect(screen.getByLabelText(ASSISTANT_COPY.composerLabel)).toBeDisabled()

    type(screen.getByLabelText(ASSISTANT_COPY.composerLabel), '¿Y el riesgo?')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('stops accepting a question longer than the server allows', () => {
    render(<Composer busy={false} online onSend={vi.fn()} />)

    const field = screen.getByLabelText(ASSISTANT_COPY.composerLabel)
    type(field, 'x'.repeat(MAX_QUESTION_CHARS + 1))
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeDisabled()
    expect(screen.getByText(/La pregunta es muy larga/)).toBeInTheDocument()

    type(field, 'x'.repeat(MAX_QUESTION_CHARS))
    expect(screen.getByRole('button', { name: ASSISTANT_COPY.sendLabel })).toBeEnabled()
  })
})