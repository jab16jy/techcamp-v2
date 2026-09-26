import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OneTimeSecret } from './OneTimeSecret'

describe('OneTimeSecret', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('copies field value to clipboard and shows success label', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })

    render(
      <OneTimeSecret
        fields={[
          { label: 'Usuario', value: 'user-1' },
          { label: 'Contraseña', value: 'pass-secret' },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Copiar usuario' }))
    expect(writeText).toHaveBeenCalledWith('user-1')
    expect(await screen.findByRole('button', { name: 'Usuario copiada' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Usuario copiada' })).toHaveTextContent('Copiado')
  })

  it('handles clipboard write rejection gracefully and displays an error message', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('Clipboard error'))
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })

    render(
      <OneTimeSecret
        fields={[
          { label: 'Contraseña', value: 'pass-secret' },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Copiar contraseña' }))

    expect(await screen.findByText('No se pudo copiar al portapapeles.')).toBeInTheDocument()
    expect(screen.queryByText('Copiado')).not.toBeInTheDocument()
  })
})
