import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PhotoField, type PhotoFieldItem } from './PhotoField'

const ITEMS: PhotoFieldItem[] = [
  { id: 'p1', previewUrl: 'blob:test/1', status: 'pending', error: null },
  { id: 'p2', previewUrl: 'blob:test/2', status: 'uploaded', error: null },
  { id: 'p3', previewUrl: 'blob:test/3', status: 'failed', error: 'bytes must be <= 204800' },
]

describe('PhotoField', () => {
  it('names the state of every photo in words, with its reason when it failed', () => {
    render(<PhotoField items={ITEMS} onPick={() => {}} onRemove={() => {}} />)

    expect(screen.getByText('Pendiente de subir')).toBeInTheDocument()
    expect(screen.getByText('Subida')).toBeInTheDocument()
    // The reason travels with the state, never the state alone (docs/07: color
    // and an icon never carry meaning on their own).
    expect(screen.getByText('No se pudo subir: bytes must be <= 204800')).toBeInTheDocument()
    expect(screen.getAllByAltText('Foto del registro')).toHaveLength(3)
  })

  it('hands the chosen files over, and removing asks for that photo only', () => {
    const onPick = vi.fn()
    const onRemove = vi.fn()
    render(<PhotoField items={ITEMS} onPick={onPick} onRemove={onRemove} />)

    const input = screen.getByLabelText('Agregar foto')
    expect(input).toHaveAttribute('accept', 'image/*')
    expect(input).toHaveAttribute('capture', 'environment')

    const files = [new File(['x'], 'a.jpg', { type: 'image/jpeg' })]
    fireEvent.change(input, { target: { files } })
    expect(onPick).toHaveBeenCalledWith(files)

    fireEvent.click(screen.getByRole('button', { name: 'Quitar foto 2' }))
    expect(onRemove).toHaveBeenCalledWith('p2')
  })

  it('shows the refusal message and no photo list while there is nothing to show', () => {
    const { rerender } = render(
      <PhotoField items={[]} onPick={() => {}} onRemove={() => {}} error="La foto es demasiado pesada" />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('La foto es demasiado pesada')
    expect(screen.queryByAltText('Foto del registro')).not.toBeInTheDocument()

    rerender(<PhotoField items={[]} onPick={() => {}} onRemove={() => {}} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
