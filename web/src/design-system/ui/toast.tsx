import * as React from 'react'
import { Toaster as SonnerToaster, toast } from 'sonner'

/**
 * Toast surface: shadcn's currently-recommended path is Sonner, not the raw
 * Radix Toast primitive. The app is light-only (no theme token duality yet),
 * so theme is fixed and the Sonner CSS variables map straight to our tokens.
 */
export function Toaster(props: React.ComponentProps<typeof SonnerToaster>) {
  return (
    <SonnerToaster
      theme="light"
      className="toaster group"
      style={
        {
          '--normal-bg': 'var(--color-surface-raised)',
          '--normal-text': 'var(--color-text)',
          '--normal-border': 'color-mix(in srgb, var(--palette-ink-green) 15%, transparent)',
          '--border-radius': 'var(--radius-md)',
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- re-exporting sonner's toast() alongside its Toaster
export { toast }
