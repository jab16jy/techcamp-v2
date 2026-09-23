import { Sheet, SheetTrigger, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '../ui/sheet'
import { StatusBand } from './StatusBand'
import { getStatusConfig, type StatusState } from './status'

export interface PressableStatusBandProps {
  status: StatusState
  /** Example sentence, e.g. "Hoy: regar 12 mm ≈ 40 min". */
  message: string
  /** The "why": rationale shown expanded in the sheet, same band, same word. */
  rationale: string
  className?: string
}

/**
 * Signature interaction (direction contract): pressing a status card lifts its
 * band into a bottom sheet with the rationale — same band, same word, expanded.
 */
export function PressableStatusBand({ status, message, rationale, className }: PressableStatusBandProps) {
  const { label } = getStatusConfig(status)
  return (
    <Sheet>
      <SheetTrigger asChild>
        <button
          type="button"
          className="block w-full rounded-lg border-0 bg-transparent p-0 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        >
          <StatusBand status={status} message={message} className={className} />
        </button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Por qué: {label}</SheetTitle>
          <SheetDescription>{rationale}</SheetDescription>
        </SheetHeader>
        <StatusBand status={status} message={message} />
      </SheetContent>
    </Sheet>
  )
}
