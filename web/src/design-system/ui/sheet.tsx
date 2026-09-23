import * as React from 'react'
import * as SheetPrimitive from '@radix-ui/react-dialog'
import { cn } from './utils'
import { Button } from './button'
import { XIcon } from './icons'
import './sheet.css'

export const Sheet = SheetPrimitive.Root
export const SheetTrigger = SheetPrimitive.Trigger
export const SheetClose = SheetPrimitive.Close

function SheetOverlay({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SheetPrimitive.Overlay>) {
  return (
    <SheetPrimitive.Overlay
      data-slot="sheet-overlay"
      className={cn('fixed inset-0 z-50 bg-text/40', className)}
      {...props}
    />
  )
}

/** Bottom sheet: pressing a status card lifts its band into this, same word, expanded. */
export function SheetContent({
  className,
  children,
  ...props
}: React.ComponentPropsWithRef<typeof SheetPrimitive.Content>) {
  return (
    <SheetPrimitive.Portal>
      <SheetOverlay />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        className={cn(
          'ds-sheet-content fixed inset-x-0 bottom-0 z-50 flex max-h-full flex-col gap-4 overflow-y-auto rounded-t-lg border-t border-text/15 bg-surface-raised p-6 pt-3 text-text shadow-lg outline-none',
          className,
        )}
        {...props}
      >
        <span aria-hidden="true" className="mx-auto h-1 w-10 shrink-0 rounded-full bg-text/20" />
        {children}
        <SheetPrimitive.Close asChild>
          <Button
            type="button"
            variant="ghost"
            className="absolute top-2 right-2 size-12 p-0"
            aria-label="Cerrar"
          >
            <XIcon className="size-5" />
          </Button>
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  )
}

export function SheetHeader({ className, ...props }: React.ComponentPropsWithRef<'div'>) {
  return <div data-slot="sheet-header" className={cn('flex flex-col gap-1', className)} {...props} />
}

export function SheetFooter({ className, ...props }: React.ComponentPropsWithRef<'div'>) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn('flex flex-col gap-2', className)}
      {...props}
    />
  )
}

export function SheetTitle({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn('font-serif text-xl text-text', className)}
      {...props}
    />
  )
}

export function SheetDescription({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn('text-sm text-text-muted', className)}
      {...props}
    />
  )
}
