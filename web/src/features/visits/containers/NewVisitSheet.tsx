import { useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { toast } from '../../../design-system/ui/toast'
import { useMe } from '../../../lib/api/me'
import { uuidv7 } from '../../../lib/db/ids'
import { saveExtensionVisit, type ExtensionVisitDraft } from '../../../lib/db/local'
import { todayInBogota } from '../components/topics'
import { VisitForm, type PlotOption } from '../components/VisitForm'

export interface FarmContext {
  id: string
  org_id: string
  name: string
}

export interface NewVisitSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  farm: FarmContext
  plots?: PlotOption[]
  technicianId?: string
}

/**
 * Container for creating an extension visit (docs/03, docs/07, D14).
 * Saves to Dexie local store and queues push via outbox (local-first, works offline).
 */
export function NewVisitSheet({
  open,
  onOpenChange,
  farm,
  plots = [],
  technicianId,
}: NewVisitSheetProps) {
  const { data: me } = useMe()
  const [visitedOn, setVisitedOn] = useState(() => todayInBogota())
  const [plotId, setPlotId] = useState<string | null>(null)
  const [selectedTopics, setSelectedTopics] = useState<string[]>([])
  const [recommendations, setRecommendations] = useState('')
  const [commitments, setCommitments] = useState('')
  const [notes, setNotes] = useState('')
  const [topicError, setTopicError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function reset() {
    setVisitedOn(todayInBogota())
    setPlotId(null)
    setSelectedTopics([])
    setRecommendations('')
    setCommitments('')
    setNotes('')
    setTopicError(null)
    setFormError(null)
    setSaving(false)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  function handleToggleTopic(topicId: string, checked: boolean) {
    setTopicError(null)
    setSelectedTopics((prev) =>
      checked ? [...prev, topicId] : prev.filter((id) => id !== topicId),
    )
  }

  async function handleSubmit() {
    setFormError(null)

    if (selectedTopics.length === 0) {
      setTopicError('Selecciona al menos un tema de la visita.')
      return
    }

    const resolvedTechId = technicianId ?? me?.id
    if (!resolvedTechId) {
      setFormError('No se pudo identificar al técnico que registra la visita.')
      return
    }

    setSaving(true)
    try {
      const draft: ExtensionVisitDraft = {
        id: uuidv7(),
        org_id: farm.org_id,
        farm_id: farm.id,
        plot_id: plotId || null,
        technician_id: resolvedTechId,
        visited_on: visitedOn,
        topics: selectedTopics,
        recommendations: recommendations.trim() || null,
        commitments: commitments.trim() || null,
        notes: notes.trim() || null,
      }

      await saveExtensionVisit(draft)
      toast('Guardado en el teléfono')
      reset()
      onOpenChange(false)
    } catch {
      setFormError('No se pudo guardar la visita en el teléfono. Intenta nuevamente.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <FormSheet
      open={open}
      onOpenChange={handleOpenChange}
      title="Nueva visita de extensión"
      description={`Registrar visita técnica a ${farm.name}.`}
      submitLabel="Guardar visita"
      onSubmit={handleSubmit}
      submitLoading={saving}
    >
      <VisitForm
        visitedOn={visitedOn}
        onVisitedOnChange={setVisitedOn}
        plots={plots}
        plotId={plotId}
        onPlotIdChange={setPlotId}
        selectedTopics={selectedTopics}
        onToggleTopic={handleToggleTopic}
        recommendations={recommendations}
        onRecommendationsChange={setRecommendations}
        commitments={commitments}
        onCommitmentsChange={setCommitments}
        notes={notes}
        onNotesChange={setNotes}
        topicError={topicError}
        formError={formError}
      />
    </FormSheet>
  )
}
