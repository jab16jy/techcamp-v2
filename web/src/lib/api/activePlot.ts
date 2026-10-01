import { useSyncExternalStore } from 'react'

/**
 * The active plot per organization (D-T0.7, docs/07 §Mapa de pantallas, Inicio
 * item 1: "la parcela activa es la última que el usuario abrió en ese
 * dispositivo, guardada por organización en el store de UI").
 *
 * A small UI store beside `session.ts`, which owns the other half of the same
 * global UI state (the active organization), and it follows the same pattern:
 * `localStorage` so it survives a reload, one JSON map keyed by org id, and
 * `useSyncExternalStore` so a screen that opens a plot elsewhere is re-rendered.
 *
 * Per organization, not global: one phone can hold a technician's account in
 * two organizations, and switching organizations must not carry the previous
 * one's plot over. The choice is the user's only while a plot is actually
 * remembered, so this never claims a plot the user did not open.
 */
const ACTIVE_PLOT_KEY = 'techcamp.active_plot'

type Listener = () => void
const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of listeners) listener()
}

/** Anything this store did not write — older format, torn write, no storage — is no plot. */
function readAll(): Record<string, string> {
  try {
    const raw = localStorage.getItem(ACTIVE_PLOT_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    const plots: Record<string, string> = {}
    for (const [orgId, plotId] of Object.entries(parsed)) {
      if (orgId && typeof plotId === 'string' && plotId) plots[orgId] = plotId
    }
    return plots
  } catch {
    return {}
  }
}

function writeAll(plots: Record<string, string>): void {
  try {
    localStorage.setItem(ACTIVE_PLOT_KEY, JSON.stringify(plots))
  } catch {
    /* private browsing / quota: the home screen falls back to the first plot */
  }
}

/** The last plot opened in this organization, or null when none is remembered. */
export function getActivePlotId(orgId: string | null): string | null {
  if (!orgId) return null
  return readAll()[orgId] ?? null
}

/** Called by every screen that opens a plot, so "the last one opened" stays true. */
export function setActivePlotId(orgId: string, plotId: string): void {
  if (!orgId || !plotId) return
  writeAll({ ...readAll(), [orgId]: plotId })
  emit()
}

/** Drops one organization's choice (a plot that no longer exists, an org left behind). */
export function forgetActivePlotId(orgId: string): void {
  const plots = readAll()
  if (!(orgId in plots)) return
  delete plots[orgId]
  writeAll(plots)
  emit()
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The remembered plot, kept in step with every `setActivePlotId` call. */
export function useActivePlotId(orgId: string | null): string | null {
  return useSyncExternalStore(subscribe, () => getActivePlotId(orgId), () => null)
}
