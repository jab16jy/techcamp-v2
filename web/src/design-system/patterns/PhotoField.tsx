import { useId } from 'react'
import { Button, buttonVariants } from '../ui/button'
import { cn } from '../ui/utils'

/**
 * The photo control of a form: "Agregar foto" plus the thumbnails of what has
 * been taken, each with its state in plain words (ADR-0018 photos the client
 * takes; docs/07 keeps connection state and pending work visible as text, never
 * as a colour or a badge alone).
 *
 * Composed only from the E1 primitives — the secondary button, the raised
 * cream list row, the 48 px touch target — so it adds no token and no new
 * visual language to the frozen design system (ADR-0006).
 */

export interface PhotoFieldItem {
  id: string
  /** Object URL of the compressed photo; null once it left the phone. */
  previewUrl: string | null
  status: 'pending' | 'uploaded' | 'failed'
  error: string | null
}

export interface PhotoFieldProps {
  items: PhotoFieldItem[]
  onPick: (files: FileList | null) => void
  onRemove: (id: string) => void
  /** Why the last chosen photo was refused (Spanish, shown under the control). */
  error?: string | null
  disabled?: boolean
}

function stateText(item: PhotoFieldItem): string {
  if (item.status === 'failed') {
    return item.error === null ? 'No se pudo subir' : `No se pudo subir: ${item.error}`
  }
  return item.status === 'uploaded' ? 'Subida' : 'Pendiente de subir'
}

export function PhotoField({ items, onPick, onRemove, error = null, disabled = false }: PhotoFieldProps) {
  const inputId = useId()

  return (
    <section aria-label="Fotos" className="flex flex-col gap-3">
      {/* The native input is what the browser gives us for the camera
          (`accept` + `capture` are the platform's own affordance); it stays in
          the accessibility tree and keyboard-reachable, and the label is what
          the finger aims at. */}
      <input
        id={inputId}
        type="file"
        accept="image/*"
        capture="environment"
        className="peer sr-only"
        disabled={disabled}
        onChange={(event) => {
          onPick(event.target.files)
          // Clearing the input is what makes choosing the same photo twice in a
          // row fire `change` again instead of being swallowed.
          event.target.value = ''
        }}
      />
      <label
        htmlFor={inputId}
        className={cn(
          buttonVariants({ variant: 'secondary' }),
          'w-fit cursor-pointer peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand',
          disabled && 'pointer-events-none opacity-50',
        )}
      >
        Agregar foto
      </label>

      {error !== null && (
        <p role="alert" className="text-sm text-severity-critical">
          {error}
        </p>
      )}

      {items.length > 0 && (
        <ul className="divide-y divide-text/10 rounded-lg bg-surface-raised">
          {items.map((item, index) => (
            <li key={item.id} className="flex items-center gap-3 p-3">
              {item.previewUrl === null ? (
                <div aria-hidden="true" className="size-16 shrink-0 rounded-md bg-text/5" />
              ) : (
                <img
                  src={item.previewUrl}
                  alt="Foto del registro"
                  className="size-16 shrink-0 rounded-md object-cover"
                />
              )}
              <p className="flex-1 text-sm">{stateText(item)}</p>
              <Button
                type="button"
                variant="ghost"
                aria-label={`Quitar foto ${index + 1}`}
                className="min-h-12 min-w-12 shrink-0 px-3 text-text-muted"
                disabled={disabled}
                onClick={() => onRemove(item.id)}
              >
                Quitar
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
