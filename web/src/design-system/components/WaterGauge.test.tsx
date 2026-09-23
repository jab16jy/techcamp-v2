import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { WaterGauge } from './WaterGauge'

describe('WaterGauge', () => {
  it('prints the label, tabular value label and clamps the bar to 0-100', () => {
    render(
      <WaterGauge
        percentage={130}
        label="Humedad de suelo · saturado"
        valueLabel="95 % de capacidad"
        status="watch"
      />,
    )

    expect(screen.getByText('Humedad de suelo · saturado')).toBeInTheDocument()
    expect(screen.getByText('95 % de capacidad')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('ends the row in its status band classification, never color alone', () => {
    render(
      <WaterGauge percentage={20} label="Humedad de suelo · seco" valueLabel="20 % de capacidad" status="stress" />,
    )

    expect(screen.getByText('Estrés')).toBeInTheDocument()
  })
})
