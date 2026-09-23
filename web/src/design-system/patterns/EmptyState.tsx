import type { ReactNode } from 'react'
import { cn } from '../ui/utils'

export interface EmptyStateProps {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
  className?: string
}

/** Teaches the interface instead of saying "nothing here" (impeccable operate guidance). */
export function EmptyState({ title, description, action, icon, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center gap-3 px-6 py-12 text-center', className)}>
      {icon && (
        <div aria-hidden="true" className="text-text-muted">
          {icon}
        </div>
      )}
      <p className="text-lg font-semibold">{title}</p>
      {description && <p className="text-base text-text-muted">{description}</p>}
      {action}
    </div>
  )
}
