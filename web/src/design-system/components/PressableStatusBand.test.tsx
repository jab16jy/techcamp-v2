import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PressableStatusBand } from './PressableStatusBand'

describe('PressableStatusBand', () => {
  it('renders the band behind an accessible button, rationale hidden until pressed', () => {
    render(
      <PressableStatusBand
        status="irrigate"
        message="Hoy: regar 12 mm ≈ 40 min."
        rationale="El balance hídrico muestra un déficit de 12 mm."
      />,
    )

    expect(screen.getByRole('button', { name: /Regar/ })).toBeInTheDocument()
    expect(screen.queryByText('El balance hídrico muestra un déficit de 12 mm.')).not.toBeInTheDocument()
  })

  it('lifts the same band into a sheet with the rationale when pressed', () => {
    render(
      <PressableStatusBand
        status="irrigate"
        message="Hoy: regar 12 mm ≈ 40 min."
        rationale="El balance hídrico muestra un déficit de 12 mm."
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Regar/ }))

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('El balance hídrico muestra un déficit de 12 mm.')).toBeInTheDocument()
    // Same band, same word, expanded: "Regar" now appears in both trigger and sheet.
    expect(screen.getAllByText('Regar').length).toBeGreaterThanOrEqual(2)
  })
})
