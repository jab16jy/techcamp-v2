import type { ComponentType } from 'react'
import type { IconProps } from '../ui/icons'
import { CheckCircleIcon, DropletIcon, AlertTriangleIcon, EyeIcon } from '../ui/icons'

/** Water-balance state for a plot, per docs/07 (`stress` is rainfed-only). */
export type StatusState = 'ok' | 'watch' | 'irrigate' | 'stress'

export interface StatusConfig {
  /** Plain word shown next to the pictogram, never color alone. */
  label: string
  /** Tailwind background utility generated from the matching `--color-status-*` token. */
  colorClass: string
  Icon: ComponentType<IconProps>
}

const STATUS_CONFIG: Record<StatusState, StatusConfig> = {
  ok: { label: 'Bien', colorClass: 'bg-status-ok', Icon: CheckCircleIcon },
  watch: { label: 'Vigilar', colorClass: 'bg-status-watch', Icon: EyeIcon },
  irrigate: { label: 'Regar', colorClass: 'bg-status-irrigate', Icon: DropletIcon },
  stress: { label: 'Estrés', colorClass: 'bg-status-stress', Icon: AlertTriangleIcon },
}

/** Status → sack-label band mapping: color, pictogram and the one plain word. */
export function getStatusConfig(status: StatusState): StatusConfig {
  return STATUS_CONFIG[status]
}
