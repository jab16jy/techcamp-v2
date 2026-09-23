import * as React from 'react'
import { cn } from './utils'

export type InputProps = React.ComponentPropsWithRef<'input'>

export function Input({ className, type = 'text', ...props }: InputProps) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'flex h-12 w-full min-w-0 rounded-md border border-text/20 bg-surface-raised px-3 text-base text-text outline-none placeholder:text-text-muted',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
        'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
        'aria-invalid:border-severity-critical aria-invalid:outline-severity-critical',
        className,
      )}
      {...props}
    />
  )
}
