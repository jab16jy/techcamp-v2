import type { ReactNode } from 'react'

/** Stand-in for a tab's real screen; feature folders per docs/07 land when the route needs one. */
export function PlaceholderPage({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 pt-6">
      <h1 className="font-serif text-2xl">{title}</h1>
      <p className="mt-2 text-base text-text-muted">Pantalla pendiente.</p>
      {children}
    </div>
  )
}
