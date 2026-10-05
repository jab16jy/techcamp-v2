import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BaselineSurvey, type SurveyCrop } from './BaselineSurvey'

const CROPS: SurveyCrop[] = [
  { id: 1, nameEs: 'Maíz' },
  { id: 2, nameEs: 'Ñame' },
]

function renderSurvey(overrides: Partial<Parameters<typeof BaselineSurvey>[0]> = {}) {
  const onSubmit = vi.fn()
  render(
    <BaselineSurvey
      open
      onOpenChange={vi.fn()}
      crops={CROPS}
      onSubmit={onSubmit}
      saving={false}
      error={null}
      {...overrides}
    />,
  )
  return onSubmit
}

async function choose(labelText: string, optionName: string) {
  fireEvent.click(screen.getByLabelText(labelText))
  fireEvent.click(await screen.findByRole('option', { name: optionName }))
}

describe('BaselineSurvey', () => {
  it('offers the four irrigation practices the server accepts, in plain Spanish', async () => {
    renderSurvey()

    // The closed vocabulary of `metrics.domain.models.IrrigationPractice`
    // (server, T2): four choices, no free text that could miss the enum.
    // Read them while the list is open — Radix unmounts it once one is picked.
    fireEvent.click(screen.getByLabelText('¿Cómo se regaba antes?'))
    for (const label of ['Secano', 'Goteo', 'Aspersión', 'Gravedad']) {
      expect(await screen.findByRole('option', { name: label })).toBeInTheDocument()
    }
  })

  it('sends the survey with the units the field names already carry', async () => {
    const onSubmit = renderSurvey()

    fireEvent.change(screen.getByLabelText('Fecha de inscripción'), {
      target: { value: '2026-01-15' },
    })
    await choose('Cultivo del último ciclo', 'Ñame')
    fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
      target: { value: '2400' },
    })
    await choose('¿Cómo se regaba antes?', 'Goteo')
    fireEvent.click(screen.getByRole('button', { name: 'Guardar encuesta' }))

    expect(onSubmit).toHaveBeenCalledWith({
      enrolled_on: '2026-01-15',
      crop_id: 2,
      last_yield_kg_ha: 2400,
      // Left empty is missing evidence, never a free plot (docs/03:426-430).
      last_cost_cop_ha: null,
      irrigation_practice: 'drip',
    })
  })

  it('keeps the submit action disabled until the required answers are in', () => {
    renderSurvey()

    const submit = screen.getByRole('button', { name: 'Guardar encuesta' })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
      target: { value: '2400' },
    })
    // Yield alone is not enough: a survey needs its crop and its practice.
    expect(submit).toBeDisabled()
  })

  it('says the cost is optional and fills a blank one as unknown', async () => {
    const onSubmit = renderSurvey()

    fireEvent.change(screen.getByLabelText('Fecha de inscripción'), {
      target: { value: '2026-01-15' },
    })
    await choose('Cultivo del último ciclo', 'Maíz')
    fireEvent.change(screen.getByLabelText(/Rendimiento del último ciclo/), {
      target: { value: '1500' },
    })
    await choose('¿Cómo se regaba antes?', 'Aspersión')
    fireEvent.click(screen.getByRole('button', { name: 'Guardar encuesta' }))

    expect(onSubmit.mock.calls[0][0].last_cost_cop_ha).toBeNull()
    expect(screen.getByText(/Costo aproximado por hectárea \(COP\/ha\), opcional/)).toBeInTheDocument()
  })

  it('starts from the recorded survey when one already exists', () => {
    renderSurvey({
      initial: {
        enrolled_on: '2026-01-15',
        crop_id: 2,
        last_yield_kg_ha: 2400,
        last_cost_cop_ha: 800000,
        irrigation_practice: 'gravity',
      },
    })

    expect(screen.getByLabelText('Fecha de inscripción')).toHaveValue('2026-01-15')
    expect(screen.getByLabelText(/Rendimiento del último ciclo/)).toHaveValue(2400)
    expect(screen.getByLabelText(/Costo aproximado/)).toHaveValue(800000)
  })

  it('shows the server message when the save is rejected', () => {
    renderSurvey({ error: 'crop_id is not a valid crop' })

    // Not color alone: the message is text the farmer can read (docs/07:12).
    expect(screen.getByText('crop_id is not a valid crop')).toBeInTheDocument()
  })
})