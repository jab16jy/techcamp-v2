import { Input } from '../../../design-system/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../../design-system/ui/select'
import { VISIT_TOPICS } from './topics'

export interface PlotOption {
  id: string
  name: string
}

export interface VisitFormProps {
  visitedOn: string
  onVisitedOnChange: (val: string) => void
  plots: PlotOption[]
  plotId: string | null
  onPlotIdChange: (val: string | null) => void
  selectedTopics: string[]
  onToggleTopic: (topicId: string, checked: boolean) => void
  recommendations: string
  onRecommendationsChange: (val: string) => void
  commitments: string
  onCommitmentsChange: (val: string) => void
  notes: string
  onNotesChange: (val: string) => void
  topicError?: string | null
  formError?: string | null
}

/**
 * Presentational form for recording an extension visit (docs/03, docs/07).
 * Pure UI with no stores or I/O.
 */
export function VisitForm({
  visitedOn,
  onVisitedOnChange,
  plots,
  plotId,
  onPlotIdChange,
  selectedTopics,
  onToggleTopic,
  recommendations,
  onRecommendationsChange,
  commitments,
  onCommitmentsChange,
  notes,
  onNotesChange,
  topicError,
  formError,
}: VisitFormProps) {
  return (
    <div className="flex flex-col gap-4">
      {formError && (
        <p role="alert" className="text-base text-severity-critical">
          {formError}
        </p>
      )}

      <label className="flex flex-col gap-2 text-base" htmlFor="visit-date">
        Fecha de la visita
        <Input
          id="visit-date"
          type="date"
          value={visitedOn}
          onChange={(e) => onVisitedOnChange(e.target.value)}
          required
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="visit-plot">
        Parcela (opcional)
        <Select
          value={plotId ?? 'none'}
          onValueChange={(val) => onPlotIdChange(val === 'none' ? null : val)}
        >
          <SelectTrigger id="visit-plot" aria-label="Parcela (opcional)">
            <SelectValue placeholder="Sin parcela específica (toda la finca)" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">Sin parcela específica (toda la finca)</SelectItem>
            {plots.map((plot) => (
              <SelectItem key={plot.id} value={plot.id}>
                {plot.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-base font-medium text-text">
          Temas de la visita (al menos uno)
        </legend>
        <div className="flex flex-col gap-2">
          {VISIT_TOPICS.map((topic) => {
            const isChecked = selectedTopics.includes(topic.id)
            return (
              <label
                key={topic.id}
                htmlFor={`topic-${topic.id}`}
                className="flex min-h-12 cursor-pointer items-start gap-3 rounded-md border border-text/15 bg-surface-raised p-3 text-base hover:bg-surface-raised/80"
              >
                <input
                  id={`topic-${topic.id}`}
                  type="checkbox"
                  className="mt-1 size-5 rounded border-text/20 accent-brand focus-visible:outline-2 focus-visible:outline-brand"
                  checked={isChecked}
                  onChange={(e) => onToggleTopic(topic.id, e.target.checked)}
                />
                <span className="text-base leading-snug text-text">{topic.label}</span>
              </label>
            )
          })}
        </div>
        {topicError && (
          <p role="alert" className="text-base text-severity-critical">
            {topicError}
          </p>
        )}
      </fieldset>

      <label className="flex flex-col gap-2 text-base" htmlFor="visit-recommendations">
        Recomendaciones
        <Input
          id="visit-recommendations"
          value={recommendations}
          onChange={(e) => onRecommendationsChange(e.target.value)}
          placeholder="Recomendaciones técnicas dadas al productor"
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="visit-commitments">
        Compromisos
        <Input
          id="visit-commitments"
          value={commitments}
          onChange={(e) => onCommitmentsChange(e.target.value)}
          placeholder="Compromisos y acuerdos para la próxima visita"
        />
      </label>

      <label className="flex flex-col gap-2 text-base" htmlFor="visit-notes">
        Notas
        <Input
          id="visit-notes"
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
          placeholder="Observaciones adicionales del técnico"
        />
      </label>
    </div>
  )
}
