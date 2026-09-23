import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MetricTile } from './MetricTile'

describe('MetricTile', () => {
  it('prints the label, tabular unit-labelled value and freshness', () => {
    render(<MetricTile label="Humedad de suelo" value={62} unit="%" lastDataMinutesAgo={12} status="ok" />)

    expect(screen.getByText('Humedad de suelo')).toBeInTheDocument()
    expect(screen.getByText('62')).toBeInTheDocument()
    expect(screen.getByText('%')).toBeInTheDocument()
    expect(screen.getByText('dato de hace 12 min')).toBeInTheDocument()
  })

  it('ends every reading in its status band classification, never color alone', () => {
    render(<MetricTile label="Temperatura" value={28.5} unit="°C" lastDataMinutesAgo={0} status="watch" />)

    expect(screen.getByText('Vigilar')).toBeInTheDocument()
  })

  it('prints "sin datos" for a reading with no data yet, still classified', () => {
    render(<MetricTile label="Último riego" value="—" lastDataMinutesAgo={null} status="watch" />)

    expect(screen.getByText('sin datos')).toBeInTheDocument()
    expect(screen.getByText('Vigilar')).toBeInTheDocument()
  })
})
