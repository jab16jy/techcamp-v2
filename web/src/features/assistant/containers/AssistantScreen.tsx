import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { MapIcon } from '../../../design-system/ui/icons'
import { useActivePlotId } from '../../../lib/api/activePlot'
import { useOrgId } from '../../../lib/api/session'
import { useAskAssistant } from '../api/useAskAssistant'
import { Composer } from '../components/Composer'
import { MessageList } from '../components/MessageList'
import { NoticeBanner } from '../components/NoticeBanner'

/**
 * Asistente (Más → Asistente, docs/07 §Asistente): a conversation about the
 * active plot, streamed token by token, with the sources each answer cites under
 * it.
 *
 * The plot comes from the shared UI store rather than being resolved here, the
 * same reason `IndicatorsScreen` reads it that way: docs/07 §Estructura forbids
 * one feature importing another, and the resolution rules live with the home
 * screen. With no plot remembered the screen says so instead of asking a question
 * it could not answer — a composer that would only return a `404` is worse than
 * an honest sentence.
 */
export function AssistantScreen() {
  const orgId = useOrgId()
  const plotId = useActivePlotId(orgId)
  const { turns, busy, notice, online, send } = useAskAssistant(plotId)

  if (plotId === null) {
    return (
      <EmptyState
        icon={<MapIcon className="size-10" />}
        title="No hay una parcela activa"
        description="El asistente responde sobre una parcela. Abrí una parcela y volvé a preguntar."
      />
    )
  }

  return (
    <div className="flex min-h-full flex-col">
      {notice !== null && <NoticeBanner notice={notice} />}

      <div className="flex-1">
        <MessageList turns={turns} busy={busy} />
      </div>

      <Composer busy={busy} online={online} onSend={send} />
    </div>
  )
}