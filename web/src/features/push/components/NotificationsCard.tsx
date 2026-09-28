import { useState } from 'react'
import { Button } from '../../../design-system/ui/button'
import { enableNotifications, type EnableNotificationsResult } from '../push'

/** docs/07: "Ajustes y notificaciones" hangs off the `Más` tab. */
const COPY: Record<EnableNotificationsResult['status'], string> = {
  subscribed: 'Listo: vas a recibir avisos aunque la app esté cerrada.',
  'already-active': 'Las notificaciones ya estaban activas en este navegador.',
  denied: 'Tu navegador no permite notificaciones. Puedes activarlas en los ajustes del navegador.',
  unsupported: 'Este navegador no admite notificaciones. Abre la app en la pantalla de inicio.',
  'not-configured': 'Las notificaciones no están disponibles en esta instalación.',
}

const FAILED_COPY = 'No se pudieron activar las notificaciones. Inténtalo de nuevo.'

/**
 * The one entry point for Web Push. Deliberately a single plain button with
 * plain-language feedback: a farmer is not choosing a notification channel
 * here, only saying yes. No new primitive and no new visual — it reuses E1's
 * `Button` and tokens as they are (design frozen).
 *
 * The button stays usable after every outcome, including a failure, because a
 * denied-then-revoked permission and a dropped request are both recoverable by
 * trying again.
 */
export function NotificationsCard() {
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleEnable() {
    setBusy(true)
    try {
      setMessage(COPY[(await enableNotifications()).status])
    } catch {
      setMessage(FAILED_COPY)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="notifications-title" className="mt-8">
      <h2 id="notifications-title" className="font-serif text-lg">
        Notificaciones
      </h2>
      <p className="mt-1 text-base text-text-muted">
        Recibe un aviso en el teléfono cuando haya una alerta, sin abrir la app.
      </p>
      <div className="mt-3">
        <Button variant="secondary" loading={busy} onClick={handleEnable}>
          Activar notificaciones
        </Button>
      </div>
      {message && (
        <p role="status" className="mt-3 text-base">
          {message}
        </p>
      )}
    </section>
  )
}
