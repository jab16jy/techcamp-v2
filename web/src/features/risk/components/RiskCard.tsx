import type { RiskEventLine } from '../api/riskCopy'

export interface RiskCardProps {
  event: RiskEventLine
}

/**
 * One risk prediction: the event, the probability, the horizon, why the model
 * says it, and where the number comes from (docs/07 §Riesgo climático).
 *
 * The card ends with the origin line, not with a severity badge. The served
 * line base has no calibrated cuts, so its severity is always `low` and says
 * nothing about the risk (D-T9.2). The card names the version instead, so the
 * producer reads a number with its origin rather than a severity nobody
 * measured (D-DH.12).
 */
export function RiskCard({ event }: RiskCardProps) {
  return (
    <article className="rounded-lg border border-text/10 bg-surface-raised p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-lg font-semibold">{event.event}</h3>
        <span className="text-xl font-semibold tabular-nums">{event.probability}</span>
      </div>

      <dl className="mt-3 divide-y divide-text/10 border-t border-text/10 text-base">
        <div className="flex gap-3 py-2">
          <dt className="w-24 shrink-0 text-text-muted">Horizonte</dt>
          <dd>{event.horizon}</dd>
        </div>
        {event.severity !== null && (
          <div className="flex gap-3 py-2">
            <dt className="w-24 shrink-0 text-text-muted">Severidad</dt>
            <dd>{event.severity}</dd>
          </div>
        )}
        {event.factors.length > 0 && (
          <div className="flex gap-3 py-2">
            <dt className="w-24 shrink-0 text-text-muted">Por qué</dt>
            <dd>
              <ul className="space-y-1">
                {event.factors.map((factor) => (
                  <li key={factor.label} className="flex justify-between gap-3">
                    <span>{factor.label}</span>
                    <span className="tabular-nums">{factor.value}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      <p className="mt-3 text-sm text-text-muted">Origen: {event.origin}</p>
    </article>
  )
}
