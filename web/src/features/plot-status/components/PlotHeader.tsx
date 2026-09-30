export interface PlotHeaderProps {
  /** The active plot's own name (docs/07, Inicio item 1). */
  plotName: string
  /** "Maíz · día 42 · desarrollo", or null when the plot has no active cycle. */
  cycleLine: string | null
}

/**
 * Inicio item 1: the active plot and its crop with its stage. The stage line
 * is omitted whole rather than half-written, so a plot with no active cycle
 * shows only its name and claims nothing about a crop.
 */
export function PlotHeader({ plotName, cycleLine }: PlotHeaderProps) {
  return (
    <header className="px-4 pt-6">
      <h1 className="font-serif text-2xl">{plotName}</h1>
      {cycleLine !== null && <p className="mt-1 text-base text-text-muted">{cycleLine}</p>}
    </header>
  )
}
