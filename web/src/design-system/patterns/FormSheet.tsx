import type { ReactNode } from 'react'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetFooter } from '../ui/sheet'
import { Button } from '../ui/button'

export interface FormSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  submitLabel: string
  onSubmit: () => void
  submitDisabled?: boolean
  submitLoading?: boolean
  cancelLabel?: string
}

/** Bottom sheet for forms, per the iOS structure (docs/07): the primary action sits at thumb reach. */
export function FormSheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  submitLabel,
  onSubmit,
  submitDisabled,
  submitLoading,
  cancelLabel = 'Cancelar',
}: FormSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription>{description}</SheetDescription>}
        </SheetHeader>
        <div className="flex flex-col gap-4">{children}</div>
        <SheetFooter>
          <Button type="button" variant="primary" onClick={onSubmit} disabled={submitDisabled} loading={submitLoading}>
            {submitLabel}
          </Button>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
