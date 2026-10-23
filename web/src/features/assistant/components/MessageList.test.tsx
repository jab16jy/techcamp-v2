/**
 * docs/07 §Asistente: the turn list, the composer and the cited sources are the
 * whole screen. The four states a low-literacy producer has to tell apart are
 * carried by these two components — text, never a code — so each one is asserted
 * here against the copy that speaks it.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AssistantCitation } from '../api/assistantStream'
import { ASSISTANT_COPY } from './assistantCopy'
import type { AssistantTurn } from '../api/useAskAssistant'
import { MessageList } from './MessageList'

const CITATION: AssistantCitation = {
  source: 'Boletín Agroclimático Nacional',
  title: 'Boletín de octubre de 2026',
  kind: 'agroclimatic_bulletin',
  source_url: 'https://example.com/boletin.pdf',
  published_on: '2026-10-01',
  similarity: 0.87,
}

function turns(...items: AssistantTurn[]): AssistantTurn[] {
  return items
}

const ANSWER: AssistantTurn = {
  role: 'assistant',
  text: 'No regaste porque la lluvia fue suficiente.',
  citations: [CITATION],
  status: 'complete',
}

const QUESTION: AssistantTurn = {
  role: 'user',
  text: '¿Por qué no regué ayer?',
  citations: [],
  status: 'complete',
}

describe('MessageList', () => {
  it('invites the first question instead of showing an empty screen', () => {
    render(<MessageList turns={[]} busy={false} />)

    expect(
      screen.getByText('Preguntá sobre tu parcela, por ejemplo: ¿por qué no regué ayer?'),
    ).toBeInTheDocument()
  })

  it('shows the question and the answer as separate turns', () => {
    render(<MessageList turns={turns(QUESTION, ANSWER)} busy={false} />)

    expect(screen.getByText('¿Por qué no regué ayer?')).toBeInTheDocument()
    expect(screen.getByText('No regaste porque la lluvia fue suficiente.')).toBeInTheDocument()
  })

  it('says Respondiendo while an answer is still arriving', () => {
    render(
      <MessageList
        turns={turns(QUESTION, { role: 'assistant', text: 'No regaste', citations: [], status: 'streaming' })}
        busy
      />,
    )

    expect(screen.getByText(ASSISTANT_COPY.answering)).toBeInTheDocument()
  })

  it('names the degraded answer instead of passing it off as a full one', () => {
    render(<MessageList turns={turns(QUESTION, { ...ANSWER, status: 'degraded' })} busy={false} />)

    expect(screen.getByText(ASSISTANT_COPY.degradedNote)).toBeInTheDocument()
  })

  it('lists the cited sources under the answer that cites them', () => {
    render(<MessageList turns={turns(QUESTION, ANSWER)} busy={false} />)

    expect(screen.getByText(ASSISTANT_COPY.sourcesTitle)).toBeInTheDocument()
    // Source and title are one line by design: a producer reads a citation, not a
    // record. The line is asserted whole so the two can never drift apart.
    expect(screen.getByText(`${CITATION.source} · ${CITATION.title}`)).toBeInTheDocument()
  })

  it('links a source that has a url and never links one that does not', () => {
    render(
      <MessageList
        turns={turns(QUESTION, {
          ...ANSWER,
          citations: [CITATION, { ...CITATION, source: 'Guía ICA', title: null, source_url: null }],
        })}
        busy={false}
      />,
    )

    expect(screen.getByRole('link', { name: new RegExp(CITATION.source) })).toHaveAttribute(
      'href',
      CITATION.source_url,
    )
    // A citation with no url is still named, so the answer is never credited to
    // nothing, but it is not dressed up as something the producer can open.
    expect(screen.getByText('Guía ICA')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Guía ICA/ })).not.toBeInTheDocument()
  })

  it('says so plainly when an answer cites nothing', () => {
    render(<MessageList turns={turns(QUESTION, { ...ANSWER, citations: [] })} busy={false} />)

    expect(screen.getByText(ASSISTANT_COPY.noSources)).toBeInTheDocument()
  })

  it('never asks for sources on the producer’s own question', () => {
    render(<MessageList turns={turns(QUESTION)} busy={false} />)

    expect(screen.queryByText(ASSISTANT_COPY.sourcesTitle)).not.toBeInTheDocument()
  })
})