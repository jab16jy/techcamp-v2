import { useState } from 'react'
import { Button } from '../../../design-system/ui/button'

export interface OneTimeField {
  label: 'Usuario' | 'Contraseña'
  value: string
}

/** A credential the server will never hand out again (docs/04-api.md:79, 83): shown once,
 * with the copy the technician needs, and never cached or stored. */
export function OneTimeSecret({ fields }: { fields: OneTimeField[] }) {
  const [copied, setCopied] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleCopy(field: OneTimeField) {
    try {
      await navigator.clipboard.writeText(field.value)
      setCopied(field.label)
      setError(null)
    } catch {
      setCopied(null)
      setError('No se pudo copiar al portapapeles.')
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-base text-severity-critical">
        Copia la contraseña ahora: se muestra una sola vez y no se volverá a mostrar.
      </p>
      {fields.map((field) => (
        <div key={field.label} className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-text-muted">{field.label}</p>
            <p className="text-base break-all">{field.value}</p>
          </div>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void handleCopy(field)}
            aria-label={
              copied === field.label ? `${field.label} copiada` : `Copiar ${field.label.toLowerCase()}`
            }
          >
            {copied === field.label ? 'Copiado' : 'Copiar'}
          </Button>
        </div>
      ))}
      {error && <p className="text-base text-severity-critical">{error}</p>}
    </div>
  )
}
