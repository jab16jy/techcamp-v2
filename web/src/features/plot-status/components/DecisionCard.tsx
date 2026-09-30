import { PressableStatusBand } from '../../../design-system/components/PressableStatusBand'
import type { Decision } from '../api/decisionCopy'

export interface DecisionCardProps {
  decision: Decision
}

/**
 * Inicio item 2: the one decision of the screen, on the design system's own
 * status band — color, pictogram and plain word together, never color alone
 * (docs/07 §Principios). Pressing it lifts the same band into the "why" sheet.
 *
 * On a rainfed plot the band carries the deficit and the two lines below it
 * carry the 7-day rain and the day's advice (ADR-0023) — a different kind of
 * advice, not a smaller irrigation depth.
 */
export function DecisionCard({ decision }: DecisionCardProps) {
  return (
    <div className="mt-4 px-4">
      <PressableStatusBand
        status={decision.status}
        message={decision.message}
        rationale={decision.rationale}
      />
      {decision.rain !== null && <p className="mt-3 text-base text-text-muted">{decision.rain}</p>}
      {decision.advice !== null && <p className="mt-1 text-base text-text">{decision.advice}</p>}
    </div>
  )
}
