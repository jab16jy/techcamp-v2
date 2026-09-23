import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('renders the home tab and the bottom tab bar', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Inicio' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Alertas/ })).toBeInTheDocument()
  })
})
