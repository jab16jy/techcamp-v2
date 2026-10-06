import type { components } from '../../../lib/api/schema'

type DigitalAdoptionIndex = components['schemas']['DigitalAdoptionIndexView']

export interface PlotHeaderProps {
  /** The active plot's own name (docs/07, Inicio item 1). */
  plotName: string
  /** "Maíz · día 42 · desarrollo", or null when the plot has no active cycle. */
  cycleLine: string | null
  /** The plot's stored adoption month, or null when it has none yet. */
  adoptionIndex: DigitalAdoptionIndex | null
}

/**
 * The stored month read as a month name, e.g. `septiembre`.
 *
 * `timeZone: 'UTC'` because the value is a date-only bucket (the month's first
 * day): formatting it in the phone's own zone shifts that UTC midnight back a
 * day on any device behind UTC, and September would be announced as August.
 *
 * Spelled here rather than imported because docs/07 §Estructura forbids one
 * feature importing another, and the indicators screen formats the same bucket
 * for the same reason — a shared `lib/date` helper is the place this belongs once
 * a lane owns that file.
 */
function monthName(month: string): string {
  return new Intl.DateTimeFormat('es-CO', { month: 'long', timeZone: 'UTC' }).format(new Date(month))
}

/**
 * Inicio item 1: the active plot and its crop with its stage. The stage line
 * is omitted whole rather than half-written, so a plot with no active cycle
 * shows only its name and claims nothing about a crop.
 *
 * Under the crop line, the discreet adoption line docs/07:152 asks for —
 * "Adopción digital: 72 · septiembre" — with the index rounded to a whole
 * number because it splits 100 points over the components that had evidence
 * (D-T0.3) and its decimals are arithmetic, not something to act on. The whole
 * line is omitted when the plot has no index: a line that claims 0 would say
 * the plot adopted nothing (D-T0.3, docs/11:57).
 */
export function PlotHeader({ plotName, cycleLine, adoptionIndex }: PlotHeaderProps) {
  return (
    <header className="px-4 pt-6">
      <h1 className="font-serif text-2xl">{plotName}</h1>
      {cycleLine !== null && <p className="mt-1 text-base text-text-muted">{cycleLine}</p>}
      {adoptionIndex !== null && (
        <p className="mt-1 text-base text-text-muted">
          {`Adopción digital: ${Math.round(adoptionIndex.value)} · ${monthName(adoptionIndex.month)}`}
        </p>
      )}
    </header>
  )
}
