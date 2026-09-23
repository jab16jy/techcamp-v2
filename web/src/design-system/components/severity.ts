import type { ComponentType } from 'react'
import type { IconProps } from '../ui/icons'
import { InfoIcon, AlertTriangleIcon, AlertOctagonIcon } from '../ui/icons'

/** Alert severity, distinct from plot water-balance status. */
export type Severity = 'info' | 'warning' | 'critical'

export interface SeverityConfig {
  label: string
  colorClass: string
  Icon: ComponentType<IconProps>
}

const SEVERITY_CONFIG: Record<Severity, SeverityConfig> = {
  info: { label: 'Información', colorClass: 'bg-severity-info', Icon: InfoIcon },
  warning: { label: 'Advertencia', colorClass: 'bg-severity-warning', Icon: AlertTriangleIcon },
  critical: { label: 'Crítico', colorClass: 'bg-severity-critical', Icon: AlertOctagonIcon },
}

export function getSeverityConfig(severity: Severity): SeverityConfig {
  return SEVERITY_CONFIG[severity]
}
