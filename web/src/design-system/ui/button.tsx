import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from './utils'

const buttonVariants = cva(
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-md px-4 text-base font-medium outline-none transition-colors disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
  {
    variants: {
      variant: {
        primary: 'bg-brand text-surface hover:bg-brand/90 active:bg-brand/80',
        secondary:
          'border border-text/20 bg-surface-raised text-text hover:bg-surface active:bg-surface',
        ghost: 'text-text hover:bg-text/5 active:bg-text/10',
      },
    },
    defaultVariants: {
      variant: 'primary',
    },
  },
)

export interface ButtonProps
  extends React.ComponentPropsWithRef<'button'>,
    VariantProps<typeof buttonVariants> {
  /** Shows a spinner and blocks interaction without hiding the label. */
  loading?: boolean
}

export function Button({
  className,
  variant,
  loading = false,
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      data-slot="button"
      className={cn(buttonVariants({ variant }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span
          aria-hidden="true"
          className="size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- cva variants live with the component they style
export { buttonVariants }
