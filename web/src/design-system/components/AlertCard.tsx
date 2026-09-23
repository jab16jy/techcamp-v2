import { cn } from '../ui/utils'
import { getSeverityConfig, type Severity } from './severity'

export interface AlertCardProps {
  severity: Severity
  title: string
  description: string
  /** Already-formatted freshness clause, e.g. from `formatFreshness`. */
  timestampLabel?: string
  className?: string
}

export function AlertCard({ severity, title, description, timestampLabel, className }: AlertCardProps) {
  const { label, colorClass, Icon } = getSeverityConfig(severity)
  return (
    <article className={cn('rounded-lg border border-text/10 bg-surface-raised p-4', className)}>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-medium text-surface-raised',
            colorClass,
          )}
        >
          <Icon className="size-4" />
          {label}
        </span>
        {timestampLabel && <span className="text-sm text-text-muted">{timestampLabel}</span>}
      </div>
      <h3 className="mt-2 text-lg font-semibold">{title}</h3>
      <p className="mt-1 text-base text-text-muted">{description}</p>
    </article>
  )
}
